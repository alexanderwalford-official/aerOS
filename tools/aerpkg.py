#!/usr/bin/env python3
"""aerpkg.py - publish AerOS packages: sign them on this computer, and send them to the server.

Run on your own computer, with the private key from aersign.py keygen. Everything is signed
here; the package server (tools/pkgserver/server.py) only keeps what it is sent, and holds
nothing secret. See docs/PACKAGES.md.

PUBLISHING TO THE PACKAGE SERVER

    python3 tools/aerpkg.py publish <program.aer> <version> <description...>
        add a package, or a new version of one: chess.aer is published as "chess"

    python3 tools/aerpkg.py unpublish <name>
        take a package off the server

    python3 tools/aerpkg.py token <token file>
        once: make the secret token that lets you publish, in that file (only you can read
        it), and print its hash, for token_hash in the server's config file

  Both publish and unpublish fetch the index the server has now, check it is signed by your
  key, change that one package's line, give it the next serial number, sign it all, and send
  it. They take these options:

    --server URL    the server (https://packages.intercon.network unless given). Only https://
                    is allowed, as the token goes with it, except http://localhost or
                    http://127.0.0.1 for a server on this computer, for testing
    --key FILE      the private key (~/aeros-packages.key unless given)
    --token FILE    the token (~/.aerpkg-token unless given)

  For example:

    python3 tools/aerpkg.py publish ~/my-packages/chess.aer 1.2 A game of chess against AerOS

A STATIC SITE INSTEAD (no server program: for testing, or any plain web server)

    python3 tools/aerpkg.py build <packages folder> <private key file> <site folder>
        sign every package listed in <packages folder>/packages.txt, and write the site

    python3 tools/aerpkg.py serve <site folder> [port]
        serve a site from this computer (port 8000 unless given), to try pkg in QEMU, where
        this computer is http://10.0.2.2:8000 (FW WEB 10.0.2.2 8000 lets AerOS reach it)

  The packages folder holds packages.txt, one package a line ("chess 1.2 A game of chess"),
  and each package's program next to it (chess.aer). Lines starting with # are skipped.

THE INDEX (what AerOS's pkg app reads)

    # AerOS packages: name, version, signature of packages/<name>.aer, description
    #serial 8
    chess 1.2 8111c088...6b0e A game of chess against AerOS

A name is 1 to 8 of a-z, 0-9, _ and - (it becomes a file name on AerOS's disk, which has 8.3
names). A version has no spaces. Signatures are 128 hex digits, as verify() wants. The serial
goes up by one with every publish; pkg remembers the highest it has seen, and won't use an
older index, so an old one can't be sent again to roll packages back. index.sig is the
signature of the whole index, so the list itself can be checked too.
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aersign import load_private  # noqa: E402  (the same key files aersign.py makes)

from cryptography.exceptions import InvalidSignature  # noqa: E402

NAME = re.compile(r'^[a-z0-9_-]{1,8}$')
HEADER = '# AerOS packages: name, version, signature of packages/<name>.aer, description'
SERIAL = re.compile(r'^#serial ([0-9]{1,9})$')
# the servers the token may go to over plain http://: only this computer
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')


# ---- publishing to the server ----------------------------------------------------------------

def printable(text):
    """True if text is only printable ASCII: what AerOS can show, and the server accepts."""
    return all(32 <= ord(c) < 127 for c in text)


def check_server(url):
    """The server's address without a / at the end, if the token may be sent to it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in LOCAL_HOSTS):
        return url.rstrip('/')
    sys.exit(f'{url}: the token can only go over https:// (or plain http:// to this computer)')


def fetch(url):
    """The bytes at a URL, or None if the server says it has nothing there (404)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'aerpkg'}),
                                    timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        sys.exit(f'{url}: the server said {error.code} {error.reason}')
    except urllib.error.URLError as error:
        sys.exit(f'{url}: can\'t reach the server ({error.reason})')


def current_index(server, key):
    """What the server lists now, checked: (serial, {name: its line}). (0, {}) if nothing has
    been published yet. Stops if the index isn't signed by this key: nothing is built on an
    index that might not be yours."""
    index = fetch(server + '/index.txt')
    signature = fetch(server + '/index.sig')
    if index is None:
        print('The server has nothing published yet: starting a new index.')
        return 0, {}
    try:
        key.public_key().verify(bytes.fromhex((signature or b'').decode().strip()), index)
    except (InvalidSignature, ValueError):
        sys.exit('The index on the server isn\'t signed by this key, so nothing was published. '
                 '(Is it the right key, and the right server?)')
    serial = 0
    lines = {}
    for line in index.decode('ascii').split('\n'):
        match = SERIAL.match(line)
        if match:
            serial = int(match.group(1))
        elif line and not line.startswith('#'):
            lines[line.split(' ')[0]] = line
    return serial, lines


def send(server, token_path, key, serial, lines, programs):
    """Make the index from the package lines, with this serial, sign it, and upload it with the
    programs that are new or changed ({name: bytes})."""
    index = '\n'.join([HEADER, f'#serial {serial}'] + [lines[n] for n in sorted(lines)]) + '\n'
    index = index.encode('ascii')
    upload = {
        'index': base64.b64encode(index).decode(),
        'index_sig': key.sign(index).hex(),
        'packages': {name: base64.b64encode(data).decode() for name, data in programs.items()},
    }
    with open(os.path.expanduser(token_path)) as f:
        token = f.read().strip()
    request = urllib.request.Request(server + '/admin/publish', data=json.dumps(upload).encode(),
                                     method='POST', headers={
                                         'Authorization': 'Bearer ' + token,
                                         'Content-Type': 'application/json',
                                         'User-Agent': 'aerpkg'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(response.read().decode().strip())
    except urllib.error.HTTPError as error:
        # the server says why it refused, in the body
        sys.exit(error.read().decode(errors='replace').strip() or f'{error.code} {error.reason}')
    except urllib.error.URLError as error:
        sys.exit(f'{server}: can\'t reach the server ({error.reason})')


def publish(options):
    path = options.program
    name = os.path.basename(path)
    if not name.endswith('.aer'):
        sys.exit(f'{path}: a package\'s program is an AerLang file, ending in .aer')
    name = name[:-len('.aer')]
    description = ' '.join(options.description)
    if not NAME.match(name):
        sys.exit(f'"{name}": a package name is 1 to 8 of a-z, 0-9, _ and - (rename the file)')
    if not options.version or ' ' in options.version or not printable(options.version):
        sys.exit('the version must have no spaces, and only plain letters, numbers and signs')
    if not description.strip() or not printable(description):
        sys.exit('the description must be plain text (letters, numbers and signs, no accents)')
    with open(path, 'rb') as f:
        program = f.read()

    server = check_server(options.server)
    key = load_private(os.path.expanduser(options.key))
    serial, lines = current_index(server, key)
    lines[name] = f'{name} {options.version} {key.sign(program).hex()} {description}'
    send(server, options.token, key, serial + 1, lines, {name: program})


def unpublish(options):
    server = check_server(options.server)
    key = load_private(os.path.expanduser(options.key))
    serial, lines = current_index(server, key)
    if options.name not in lines:
        sys.exit(f'The server has no package called {options.name}.')
    del lines[options.name]
    send(server, options.token, key, serial + 1, lines, {})


def token(options):
    path = os.path.expanduser(options.file)
    if os.path.exists(path):
        sys.exit(f'{path} is there already: token never writes over one')
    value = secrets.token_urlsafe(32)
    # readable by its owner only, before the token goes in it
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as f:
        f.write(value + '\n')
    print(f'The token is in {path}: keep it secret (it is not the signing key).')
    print('Its hash, for "token_hash = " in the server\'s config file:')
    print(hashlib.sha256(value.encode()).hexdigest())


# ---- a static site ---------------------------------------------------------------------------

def read_list(folder):
    """The packages in packages.txt: (name, version, description) for each line."""
    packages = []
    with open(os.path.join(folder, 'packages.txt')) as f:
        for number, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(' ', 2)
            if len(parts) < 3:
                sys.exit(f'packages.txt line {number}: needs a name, a version and a description')
            name, version, description = parts
            if not NAME.match(name):
                sys.exit(f'packages.txt line {number}: "{name}" must be 1 to 8 of a-z, 0-9, _ and -')
            if not os.path.isfile(os.path.join(folder, name + '.aer')):
                sys.exit(f'packages.txt line {number}: there is no {name}.aer next to it')
            packages.append((name, version, description))
    if not packages:
        sys.exit('packages.txt lists no packages')
    return packages


def build(options):
    folder, site = options.folder, options.site
    key = load_private(options.key)
    packages = read_list(folder)
    os.makedirs(os.path.join(site, 'packages'), exist_ok=True)

    # each package's program, signed, and a line for it in the index
    lines = [HEADER]
    for name, version, description in packages:
        source = os.path.join(folder, name + '.aer')
        with open(source, 'rb') as f:
            signature = key.sign(f.read()).hex()
        shutil.copyfile(source, os.path.join(site, 'packages', name + '.aer'))
        lines.append(f'{name} {version} {signature} {description}')
        print(f'  {name} {version}')

    # the index, and its own signature (no line break after it: pkg compares it as it is)
    index = ('\n'.join(lines) + '\n').encode()
    with open(os.path.join(site, 'index.txt'), 'wb') as f:
        f.write(index)
    with open(os.path.join(site, 'index.sig'), 'w') as f:
        f.write(key.sign(index).hex())
    print(f'{len(packages)} package(s) signed into {site}: put everything in it on the server.')


def serve(options):
    os.chdir(options.site)
    port = options.port
    print(f'Serving {options.site} on port {port}: in QEMU, http://10.0.2.2:{port}/ (FW WEB 10.0.2.2 {port})')
    http.server.ThreadingHTTPServer(('127.0.0.1', port), http.server.SimpleHTTPRequestHandler).serve_forever()


# ---- which command ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(usage=__doc__, add_help=False)
    commands = parser.add_subparsers(dest='command')

    def server_options(command):
        command.add_argument('--server', default='https://packages.intercon.network')
        command.add_argument('--key', default='~/aeros-packages.key')
        command.add_argument('--token', default='~/.aerpkg-token')

    command = commands.add_parser('publish')
    command.add_argument('program')
    command.add_argument('version')
    command.add_argument('description', nargs='+')
    server_options(command)
    command.set_defaults(run=publish)

    command = commands.add_parser('unpublish')
    command.add_argument('name')
    server_options(command)
    command.set_defaults(run=unpublish)

    command = commands.add_parser('token')
    command.add_argument('file')
    command.set_defaults(run=token)

    command = commands.add_parser('build')
    command.add_argument('folder')
    command.add_argument('key')
    command.add_argument('site')
    command.set_defaults(run=build)

    command = commands.add_parser('serve')
    command.add_argument('site')
    command.add_argument('port', nargs='?', type=int, default=8000)
    command.set_defaults(run=serve)

    options = parser.parse_args()
    if options.command is None:
        sys.exit(__doc__)
    options.run(options)


if __name__ == '__main__':
    main()
