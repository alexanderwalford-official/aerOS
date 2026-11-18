#!/usr/bin/env python3
"""server.py - the AerOS package server: hands out signed packages, and takes new ones.

It keeps everything in one SQLite database, and serves it to AerOS's pkg app exactly as it was
signed. It never signs anything, and it doesn't have the private key: every package and every
index is signed on the maker's computer (tools/aerpkg.py publish) and uploaded here already
signed. What it does hold is the PUBLIC key, so it can check each upload before keeping it.

    python3 tools/pkgserver/server.py <config file>

THE CONFIG FILE (one "name = value" a line, # for comments)

    database   = /var/lib/aerpkg/packages.db    where everything is kept (made if missing)
    public_key = bea63593...                    the 64 hex digits in src/sys/package_key.h
    token_hash = 5f2b...                        what "aerpkg.py token" printed (not the token)
    port       = 8080                           the port to listen on, on 127.0.0.1 only
    page       = /opt/aerpkg/index.html         the landing page (optional)

WHAT IT ANSWERS

    GET  /  or  /index.html       the landing page (it reads index.txt in the browser)
    GET  /index.txt               the signed list of packages, byte for byte as it was signed
    GET  /index.sig               its signature (128 hex digits, no line break)
    GET  /packages/<name>.aer     a package's program
    GET  /packages/<name>/<file>  one of the files a package comes with (a picture, a video...)
    POST /admin/publish           a new signed index, and any programs and files that are new
                                  or changed

It only listens on 127.0.0.1: nginx stands in front of it (tools/pkgserver/nginx.conf). nginx
passes the GETs through on plain http:// (AerOS can't do HTTPS, and doesn't need to, as
everything it fetches is signed), and only lets /admin/ through on https://.

HOW AN UPLOAD IS CHECKED (check_upload below)

    1. The token: the request must carry "Authorization: Bearer <token>", and the token's
       SHA-256 must be token_hash. (Only the hash is kept here, so reading this config file
       doesn't give anyone the token.)
    2. The index is signed by the packages' key.
    3. Its "#serial <number>" is higher than the one being served now. So an old upload, even a
       genuine one, can't be sent again to roll the packages back.
    4. Every package line is well formed, and every package it lists has a program (uploaded
       now, or already here) that matches the signature on its line. The same for every file
       its "#file <package> <file> <signature>" lines list.
    5. Nothing is uploaded that the index doesn't list.

Only if all of that holds is anything changed, all at once in one transaction: the new
programs and files are kept, those the new index no longer lists are deleted, and the new
index replaces the old one. So the site is never half updated, and someone who steals the token
still can't publish anything that wasn't signed with the private key.
"""

import base64
import hashlib
import hmac
import http.server
import json
import re
import sqlite3
import sys
import time
import urllib.parse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# a package name: it becomes /apps/<name>.aer on AerOS's disk, which has 8.3 file names
NAME = re.compile(r'^[a-z0-9_-]{1,8}$')
# a file a package comes with: an 8.3 name in lower case, as pkg.aer writes it to AerOS's disk
# (/apps/<name>/<file>). Never a program (.aer): only a package's own program is shown to you,
# with scan(), before you install it
FILE_NAME = re.compile(r'^[a-z0-9_-]{1,8}\.[a-z0-9]{1,3}$')
# a signature: 128 hex digits, as verify() in AerLang wants
SIGNATURE = re.compile(r'^[0-9a-f]{128}$')
# the serial line: "#serial 42"
SERIAL = re.compile(r'^#serial ([0-9]{1,9})$')
# the biggest program AerOS will fetch (HTTP_BODY_MAX in src/sys/http.h), and so the biggest
# index too
FILE_MAX = 4 * 1024 * 1024
# the biggest upload: a 4 MB program or file grows by a third in base64, plus the index
UPLOAD_MAX = 8 * 1024 * 1024

CONFIG = {}   # the config file's settings, read once at the start (read_config)


class Refused(Exception):
    """An upload that doesn't pass a check: the message says which, and is sent back."""


# ---- the database ----------------------------------------------------------------------------

def open_database():
    """A connection to the database, with its tables made if they aren't there yet. Each request
    opens its own (a SQLite connection can't be shared between the server's threads)."""
    db = sqlite3.connect(CONFIG['database'], timeout=10, isolation_level=None)
    db.executescript('''
        -- each package's program, exactly as it was signed
        CREATE TABLE IF NOT EXISTS packages (
            name    TEXT PRIMARY KEY,
            program BLOB NOT NULL
        );
        -- the files packages come with, exactly as they were signed
        CREATE TABLE IF NOT EXISTS files (
            package TEXT NOT NULL,
            name    TEXT NOT NULL,
            data    BLOB NOT NULL,
            PRIMARY KEY (package, name)
        );
        -- the index being served, and its signature: one row (id 1), or none before the first
        -- publish
        CREATE TABLE IF NOT EXISTS site (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            serial    INTEGER NOT NULL,
            index_txt BLOB NOT NULL,
            index_sig TEXT NOT NULL
        );
        -- a line for every publish, to look back at what happened when
        CREATE TABLE IF NOT EXISTS publishes (
            serial   INTEGER NOT NULL,
            at       TEXT NOT NULL,
            address  TEXT NOT NULL,
            uploaded TEXT NOT NULL
        );
    ''')
    return db


# ---- checking an upload ----------------------------------------------------------------------

def signed(data, signature_hex):
    """True if signature_hex is the packages' key's signature of data."""
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(CONFIG['public_key']))
    try:
        key.verify(bytes.fromhex(signature_hex), data)
        return True
    except InvalidSignature:
        return False


def good_file(name):
    """Is this a name a package's file may have?"""
    return bool(FILE_NAME.match(name)) and not name.endswith('.aer')


def read_index(index):
    """The serial number, package lines and file lines of an index, as (serial, {name: signature},
    {(package, file): signature}). The rules are the ones pkg.aer reads it by: lines are split at
    '\\n'; blank lines and other lines starting with # are skipped; a package line is "name
    version signature description", and a file line "#file package file signature"."""
    try:
        text = index.decode('ascii')
    except UnicodeDecodeError:
        raise Refused('the index must be plain ASCII text')
    serial = None
    packages = {}
    files = {}
    for number, line in enumerate(text.split('\n'), start=1):
        # only printable characters: no tabs, and no '\r', which pkg would take as part of a line
        if any(not (32 <= ord(c) < 127) for c in line):
            raise Refused(f'index line {number} has a character that isn\'t printable')
        if line.startswith('#serial'):
            match = SERIAL.match(line)
            if match is None or serial is not None:
                raise Refused(f'index line {number}: there must be one "#serial <number>" line')
            serial = int(match.group(1))
            continue
        if line.startswith('#file'):
            parts = line.split(' ')
            if len(parts) != 4 or parts[0] != '#file':
                raise Refused(f'index line {number}: a file line is "#file <package> <file> <signature>"')
            package, file, signature = parts[1], parts[2], parts[3]
            if not NAME.match(package) or not good_file(file):
                raise Refused(f'index line {number}: "{file}" isn\'t a name a package\'s file can have '
                              f'(8.3, lower case, not .aer)')
            if not SIGNATURE.match(signature):
                raise Refused(f'index line {number}: the signature must be 128 hex digits')
            if (package, file) in files:
                raise Refused(f'index line {number}: {package}/{file} is listed twice')
            files[(package, file)] = signature
            continue
        if line == '' or line.startswith('#'):
            continue
        parts = line.split(' ')
        if len(parts) < 4 or '' in parts[:4]:
            raise Refused(f'index line {number}: needs a name, a version, a signature and a description')
        name, version, signature = parts[0], parts[1], parts[2]
        if not NAME.match(name):
            raise Refused(f'index line {number}: "{name}" must be 1 to 8 of a-z, 0-9, _ and -')
        if not SIGNATURE.match(signature):
            raise Refused(f'index line {number}: the signature must be 128 hex digits')
        if name in packages:
            raise Refused(f'index line {number}: {name} is listed twice')
        packages[name] = signature
    if serial is None:
        raise Refused('the index has no "#serial <number>" line')
    for package, file in files:
        if package not in packages:
            raise Refused(f'the index lists the file {package}/{file}, but no package {package}')
    return serial, packages, files


def check_upload(db, upload):
    """Check an upload (the JSON the publish command sends, already parsed) against the rules
    at the top of this file. Gives back (serial, index, signature, {name: program} uploaded,
    {(package, file): data} uploaded, names listed, files listed), or raises Refused saying what
    is wrong. Changes nothing."""
    if not isinstance(upload, dict):
        raise Refused('the upload must be a JSON object')
    try:
        index = base64.b64decode(upload['index'], validate=True)
        index_sig = upload['index_sig']
        programs = {name: base64.b64decode(data, validate=True)
                    for name, data in upload.get('packages', {}).items()}
        uploaded_files = {tuple(path.split('/', 1)): base64.b64decode(data, validate=True)
                          for path, data in upload.get('files', {}).items()}
    except (KeyError, TypeError, AttributeError, ValueError):
        raise Refused('the upload needs "index" (base64), "index_sig" (hex) and "packages" '
                      '({name: base64}), and may have "files" ({"name/file": base64})')
    # (checked before any name is put in a message or the log)
    if not all(NAME.match(name) for name in programs):
        raise Refused('an uploaded package\'s name isn\'t 1 to 8 of a-z, 0-9, _ and -')
    if not all(len(path) == 2 and NAME.match(path[0]) and good_file(path[1]) for path in uploaded_files):
        raise Refused('an uploaded file isn\'t "<package>/<file>", with an 8.3 name (not .aer)')
    if not isinstance(index_sig, str) or not SIGNATURE.match(index_sig):
        raise Refused('index_sig must be 128 hex digits')
    if len(index) > FILE_MAX:
        raise Refused('the index is too big for AerOS to fetch')

    # 2. signed with the packages' key
    if not signed(index, index_sig):
        raise Refused('the index isn\'t signed by the packages\' key')

    # 3. newer than the one being served
    serial, listed, listed_files = read_index(index)
    row = db.execute('SELECT serial FROM site WHERE id = 1').fetchone()
    current = row[0] if row else 0
    if serial <= current:
        raise Refused(f'the index\'s serial is {serial}, but {current} is being served already: '
                      f'it must be higher')

    # 4. every package listed has a program that matches its line
    for name, signature in listed.items():
        if name in programs:
            program = programs[name]
            if len(program) > FILE_MAX:
                raise Refused(f'{name} is too big for AerOS to fetch')
        else:
            row = db.execute('SELECT program FROM packages WHERE name = ?', (name,)).fetchone()
            if row is None:
                raise Refused(f'the index lists {name}, but it wasn\'t uploaded and isn\'t here')
            program = row[0]
        if not signed(program, signature):
            raise Refused(f'{name}\'s program doesn\'t match the signature on its line')
    # ...and every file listed
    for (package, file), signature in listed_files.items():
        if (package, file) in uploaded_files:
            data = uploaded_files[(package, file)]
            if len(data) > FILE_MAX:
                raise Refused(f'{package}/{file} is too big for AerOS to fetch')
        else:
            row = db.execute('SELECT data FROM files WHERE package = ? AND name = ?',
                             (package, file)).fetchone()
            if row is None:
                raise Refused(f'the index lists {package}/{file}, but it wasn\'t uploaded and isn\'t here')
            data = row[0]
        if not signed(data, signature):
            raise Refused(f'{package}/{file} doesn\'t match the signature on its line')

    # 5. nothing uploaded that isn't listed
    for name in programs:
        if name not in listed:
            raise Refused(f'{name} was uploaded, but the index doesn\'t list it')
    for package, file in uploaded_files:
        if (package, file) not in listed_files:
            raise Refused(f'{package}/{file} was uploaded, but the index doesn\'t list it')
    return serial, index, index_sig, programs, uploaded_files, list(listed), list(listed_files)


def publish(upload, address):
    """Check an upload, and if it passes, make it what the server serves, all at once.
    Gives back a line saying what was done, or raises Refused."""
    db = open_database()
    try:
        # BEGIN IMMEDIATE locks the database for writing now, so two uploads at once can't
        # both pass the serial check against the same old serial
        db.execute('BEGIN IMMEDIATE')
        try:
            serial, index, index_sig, programs, files, listed, listed_files = check_upload(db, upload)
            for name, program in programs.items():
                db.execute('INSERT OR REPLACE INTO packages (name, program) VALUES (?, ?)',
                           (name, program))
            for (package, file), data in files.items():
                db.execute('INSERT OR REPLACE INTO files (package, name, data) VALUES (?, ?, ?)',
                           (package, file, data))
            # programs and files the new index doesn't list any more
            gone = [name for (name,) in db.execute('SELECT name FROM packages')
                    if name not in listed]
            for name in gone:
                db.execute('DELETE FROM packages WHERE name = ?', (name,))
            for package, file in db.execute('SELECT package, name FROM files').fetchall():
                if (package, file) not in listed_files:
                    db.execute('DELETE FROM files WHERE package = ? AND name = ?', (package, file))
            db.execute('INSERT OR REPLACE INTO site (id, serial, index_txt, index_sig) '
                       'VALUES (1, ?, ?, ?)', (serial, index, index_sig))
            db.execute('INSERT INTO publishes (serial, at, address, uploaded) VALUES (?, ?, ?, ?)',
                       (serial, time.strftime('%Y-%m-%d %H:%M:%S'), address,
                        ' '.join(sorted(programs) + sorted(f'{p}/{f}' for p, f in files)) or '-'))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
    finally:
        db.close()
    message = f'Published serial {serial}: {len(listed)} package(s) listed'
    if programs or files:
        message += ', uploaded ' + ', '.join(sorted(programs) + sorted(f'{p}/{f}' for p, f in files))
    if gone:
        message += ', removed ' + ', '.join(gone)
    return message + '.'


# ---- the web side ----------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    """One request: a GET for something AerOS (or a browser) wants, or a POST to publish."""

    server_version = 'aerpkg'
    sys_version = ''

    def send(self, status, body, content_type='text/plain; charset=us-ascii'):
        """Answer with a status and a body, all of it at once, with its length (AerOS reads
        Content-Length), then close the connection."""
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        # the index changes whenever something is published: browsers should ask again
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def address(self):
        """Who sent the request: nginx says in X-Real-IP (to this server, everyone is nginx)."""
        return self.headers.get('X-Real-IP') or self.client_address[0]

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        db = open_database()
        try:
            if path in ('/', '/index.html') and CONFIG.get('page'):
                with open(CONFIG['page'], 'rb') as f:
                    self.send(200, f.read(), 'text/html; charset=utf-8')
                return
            if path in ('/index.txt', '/index.sig'):
                row = db.execute('SELECT index_txt, index_sig FROM site WHERE id = 1').fetchone()
                if row is None:
                    self.send(404, 'Nothing has been published yet.\n')
                elif path == '/index.txt':
                    self.send(200, row[0])
                else:
                    self.send(200, row[1])
                return
            match = re.fullmatch(r'/packages/([a-z0-9_-]{1,8})\.aer', path)
            if match:
                row = db.execute('SELECT program FROM packages WHERE name = ?',
                                 (match.group(1),)).fetchone()
                if row is not None:
                    self.send(200, row[0])
                    return
            match = re.fullmatch(r'/packages/([a-z0-9_-]{1,8})/([a-z0-9_-]{1,8}\.[a-z0-9]{1,3})', path)
            if match:
                row = db.execute('SELECT data FROM files WHERE package = ? AND name = ?',
                                 (match.group(1), match.group(2))).fetchone()
                if row is not None:
                    self.send(200, row[0], 'application/octet-stream')
                    return
            self.send(404, 'No such page.\n')
        finally:
            db.close()

    do_HEAD = do_GET

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != '/admin/publish':
            self.send(404, 'No such page.\n')
            return

        # 1. the token, before the upload is even read
        given = self.headers.get('Authorization', '')
        token = given[len('Bearer '):] if given.startswith('Bearer ') else ''
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        # compare_digest takes as long whatever the hashes are, so timing gives nothing away
        if not token or not hmac.compare_digest(token_hash, CONFIG['token_hash']):
            self.log_message('refused a publish from %s: wrong token', self.address())
            self.send(401, 'Refused: wrong or missing token.\n')
            return

        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            self.send(411, 'Refused: the upload needs a Content-Length.\n')
            return
        if not 0 < length <= UPLOAD_MAX:
            self.send(413, 'Refused: the upload is too big.\n')
            return

        try:
            upload = json.loads(self.rfile.read(length))
            message = publish(upload, self.address())
        except (ValueError, Refused) as problem:   # bad JSON is a ValueError too
            self.log_message('refused a publish from %s: %s', self.address(), problem)
            self.send(400, f'Refused: {problem}.\n')
            return
        self.log_message('%s (from %s)', message, self.address())
        self.send(200, message + '\n')


# ---- starting up -----------------------------------------------------------------------------

def read_config(path):
    """Read the config file into CONFIG, and check it has what the server needs."""
    with open(path) as f:
        for number, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                sys.exit(f'{path} line {number}: should be "name = value"')
            name, value = line.split('=', 1)
            CONFIG[name.strip()] = value.strip()
    for name in ('database', 'public_key', 'token_hash', 'port'):
        if not CONFIG.get(name):
            sys.exit(f'{path} has no "{name} = ..." line')
    if not re.fullmatch(r'[0-9a-f]{64}', CONFIG['public_key']):
        sys.exit(f'{path}: public_key must be 64 hex digits (from src/sys/package_key.h)')
    if not re.fullmatch(r'[0-9a-f]{64}', CONFIG['token_hash']):
        sys.exit(f'{path}: token_hash must be 64 hex digits (what "aerpkg.py token" printed)')


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    read_config(sys.argv[1])
    open_database().close()   # make the tables now, so a mistake in the path shows at once
    port = int(CONFIG['port'])
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'AerOS package server on http://127.0.0.1:{port}/, database {CONFIG["database"]}',
          flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
