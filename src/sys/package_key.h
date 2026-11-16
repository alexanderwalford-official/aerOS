/*
    package_key.h - the public key AerOS trusts packages from

    Packages are signed with a private key that only their maker has, and checked with the
    public key below (Ed25519, lib/ed25519.h). A program gets it with packagekey(), and checks a
    package with verify(data, signature, packagekey()). A package signed with any other key, or
    changed by a single byte on the way, fails the check.

    It is built into AerOS, rather than kept in a file, so that no program can swap in a key of
    its own: changing it means building AerOS again.

    SETTING IT
    It is empty until the packages' maker sets it, and verify() refuses an empty key. On your own
    computer (never on the package server):

        python3 tools/aersign.py keygen ~/aeros-packages.key

    makes the key pair, keeps the private key in that file (back it up, and never put it in this
    repository or on the server), and prints the public key: 64 hex digits. Put those between
    the quotes below and build AerOS again. tools/aersign.py sign then signs each package.
*/
#ifndef PACKAGE_KEY_H
#define PACKAGE_KEY_H

#define AEROS_PACKAGE_KEY ""

#endif /* PACKAGE_KEY_H */
