#!/usr/bin/env python3
"""aerpkg.py - publish AerOS packages: sign them, and lay them out for the package server.

Run on your own computer, with the private key from aersign.py keygen. It makes a folder to put
on any plain web server (packages.intercon.network, or one on this computer for testing); the
server only hands out files, and holds nothing secret.

    python3 tools/aerpkg.py build <packages folder> <private key file> <site folder>
        sign every package listed in <packages folder>/packages.txt, and write the site

    python3 tools/aerpkg.py serve <site folder> [port]
        serve a site from this computer (port 8000 unless given), to try pkg in QEMU, where
        this computer is http://10.0.2.2:8000 (FW WEB 10.0.2.2 8000 lets AerOS reach it)

THE PACKAGES FOLDER (yours, on your computer)

    packages.txt     one package a line: its name, version and description
                         chess 1.2 A game of chess against AerOS
                         notes 0.3 Keep notes, with a search
    chess.aer        each package's program, named after it
    notes.aer

A name is 1 to 8 of a-z, 0-9, _ and - (it becomes a file name on AerOS's disk, which has 8.3
names). A version has no spaces. Lines starting with # are skipped.

THE SITE (what goes on the server)

    index.txt               one line a package: name, version, the signature of its file, and
                            its description (signatures are 128 hex digits, as verify() wants)
    index.sig               the signature of index.txt, so the list itself can be checked
    packages/chess.aer      each package's program

AerOS's pkg app fetches index.txt and index.sig, checks the list against the key built into
AerOS (packagekey()), then checks each package it installs against its line. Nothing that wasn't
signed with your private key gets past it.
"""

import http.server
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aersign import load_private  # noqa: E402  (the same key files aersign.py makes)

NAME = re.compile(r'^[a-z0-9_-]{1,8}$')


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


def build(folder, key_path, site):
    key = load_private(key_path)
    packages = read_list(folder)
    os.makedirs(os.path.join(site, 'packages'), exist_ok=True)

    # each package's program, signed, and a line for it in the index
    lines = ['# AerOS packages: name, version, signature of packages/<name>.aer, description']
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


def serve(site, port):
    os.chdir(site)
    print(f'Serving {site} on port {port}: in QEMU, http://10.0.2.2:{port}/ (FW WEB 10.0.2.2 {port})')
    http.server.ThreadingHTTPServer(('127.0.0.1', port), http.server.SimpleHTTPRequestHandler).serve_forever()


def main():
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == 'build':
        build(args[1], args[2], args[3])
    elif len(args) in (2, 3) and args[0] == 'serve':
        serve(args[1], int(args[2]) if len(args) == 3 else 8000)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
