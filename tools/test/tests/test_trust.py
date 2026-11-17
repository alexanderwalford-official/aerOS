"""verify(), packagekey() and scan(): deciding whether to trust something (src/lang/bi_trust.c,
src/lib/ed25519.c). The signatures are made here, by Python's cryptography package and by
tools/aersign.py, so AerOS's checking is tested against another implementation, not itself."""

import os
import subprocess
import sys

from aeros import Skip

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'aersign.py')


def let(aeros, name, text):
    """Set an AerLang variable to some text at the prompt. A prompt line holds at most 127
    characters and a signature alone is 128 hex digits, so it goes in 64 at a time."""
    aeros.run(f'let {name} = "{text[:64]}"')
    for start in range(64, len(text), 64):
        aeros.run(f'{name} = {name} + "{text[start:start + 64]}"')


def test_verify_and_scan(t):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        raise Skip("needs Python's cryptography package: pip install cryptography")

    # a key pair, and a stranger's, made here
    key = Ed25519PrivateKey.generate()
    raw = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = key.public_key().public_bytes(*raw).hex()
    stranger = Ed25519PrivateKey.generate().public_key().public_bytes(*raw).hex()
    signed = key.sign(b'hello').hex()

    # a file with every byte value in it (zeros too), signed with tools/aersign.py
    data = bytes(range(256)) * 20
    with open(t.path('data.bin'), 'wb') as f:
        f.write(data)
    key_file = t.path('test.key')
    made = subprocess.run([sys.executable, TOOL, 'keygen', key_file], capture_output=True, text=True)
    tool_public = made.stdout.strip().split('\n')[-1]
    tool_signature = subprocess.run([sys.executable, TOOL, 'sign', key_file, t.path('data.bin')],
                                    capture_output=True, text=True).stdout.strip()

    disk = t.disk(files={'/data.bin': t.path('data.bin'), '/bin/phish.aer': t.files + '/phish.aer'})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.expect_printed('[ OK ] Crypto self-test (SHA-256, SHA-512, HMAC, PBKDF2, AES-256, Ed25519)')

    let(aeros, 'sig', signed)
    let(aeros, 'key', public)
    let(aeros, 'other', stranger)
    let(aeros, 'changed', signed[:-2] + ('00' if signed[-2:] != '00' else '01'))

    # the right signature, and every way of it being wrong
    aeros.cls()
    aeros.run('verify("hello", sig, key)')
    aeros.expect_line('true')
    aeros.cls()
    aeros.run('verify("hellO", sig, key)')            # one letter of the text changed
    aeros.run('verify("hello", sig, other)')          # someone else's key
    aeros.run('verify("hello", changed, key)')        # the signature changed
    aeros.run('verify("hello", "abc", key)')          # not a signature at all
    shown = [row.strip() for row in aeros.text().split('\n')]
    if shown.count('false') != 4 or 'true' in shown:
        aeros.fail('each wrong signature should give false')

    # bytes, signed by the tool, checked from a file
    let(aeros, 'tool_sig', tool_signature)
    let(aeros, 'tool_key', tool_public)
    aeros.cls()
    aeros.run('verify(rdb("/data.bin"), tool_sig, tool_key)')
    aeros.expect_line('true')

    # the key built into AerOS (src/sys/package_key.h): 64 hex digits, and a signature made
    # with this test's own key doesn't check out against it
    aeros.cls()
    aeros.run('len(packagekey())')
    aeros.run('verify("hello", sig, packagekey())')
    aeros.expect_line('64', 'false')

    # scan(): what a program can do, and the tricks in it, as lists
    aeros.cls()
    aeros.run('let found = scan(rdf("/bin/phish.aer"))')
    aeros.run('print(len(found[0]), len(found[1]))')
    aeros.expect_line('5 3')
    aeros.run('print(found[0][1])')
    aeros.run('print(found[1][0])')
    aeros.expect_printed('delete files (delf): asks you first (UNDO brings back files from your home folder)',
                         "line 4: it prints something that looks like AerOS's own questions")
    aeros.cls()
    aeros.run('print(len(scan("print(1 + 2)")[0]))')
    aeros.expect_line('0')
