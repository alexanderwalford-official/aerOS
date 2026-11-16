#!/usr/bin/env python3
"""aersign.py - make the key that signs AerOS packages, and sign files with it.

Run on your own computer, never on the package server: the private key must stay where only you
can get at it. AerOS only ever checks signatures, with the public key built into it
(src/sys/package_key.h), using verify(data, signature, key) in AerLang.

    python3 tools/aersign.py keygen ~/aeros-packages.key
        make a new key pair: the private key goes in that file (only you can read it), and the
        public key is printed, 64 hex digits, to put in src/sys/package_key.h

    python3 tools/aersign.py public ~/aeros-packages.key
        print the public key of a private key file again

    python3 tools/aersign.py sign ~/aeros-packages.key chess.aer
        print the signature of a file: 128 hex digits, for the package index

    python3 tools/aersign.py check <public key> chess.aer <signature>
        check a signature here, the same way AerOS does

The keys are Ed25519 (RFC 8032), the same as AerOS's lib/ed25519.c. Needs Python's
"cryptography" package (pip install cryptography), as tools/aernet.py does.

Keep a backup of the private key file somewhere safe and offline. Without it, no new package
can be signed; and anyone who gets it can sign packages every AerOS would trust. Never commit
it to the repository.
"""

import os
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def load_private(path):
    """The private key in a file made by keygen (its 32 byte seed, as hex)."""
    with open(path) as f:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(f.read().strip()))


def public_hex(private_key):
    """The public key that goes with a private key, as 64 hex digits."""
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def keygen(path):
    if os.path.exists(path):
        sys.exit(f'{path} is there already: keygen never writes over a key (it could be the only copy)')
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    # made readable by its owner only, before the key goes in it
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as f:
        f.write(seed.hex() + '\n')
    print(f'The private key is in {path}: back it up, keep it secret, never put it in the repository.')
    print('The public key, for src/sys/package_key.h:')
    print(public_hex(key))


def sign(key_path, file_path):
    with open(file_path, 'rb') as f:
        print(load_private(key_path).sign(f.read()).hex())


def check(public, file_path, signature):
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public))
    with open(file_path, 'rb') as f:
        data = f.read()
    try:
        key.verify(bytes.fromhex(signature), data)
        print('Good: signed by that key, and not changed since.')
    except InvalidSignature:
        sys.exit('Bad: not signed by that key, or changed since.')


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == 'keygen':
        keygen(args[1])
    elif len(args) == 2 and args[0] == 'public':
        print(public_hex(load_private(args[1])))
    elif len(args) == 3 and args[0] == 'sign':
        sign(args[1], args[2])
    elif len(args) == 4 and args[0] == 'check':
        check(args[1], args[2], args[3])
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
