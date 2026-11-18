/*
    ed25519.h - checking a signature: was this really signed by the holder of a private key?

    Ed25519 is a "public key" signature scheme (RFC 8032). Whoever makes something has a pair of
    keys: a private key, kept secret, and a public key, given to everyone. Signing a file with
    the private key gives a 64 byte signature. Anyone with the public key can then check it:

        on the maker's computer                      in AerOS
        -----------------------                      --------
        signature = sign(private key, file)   -->    ed25519_verify(signature, file, public key)
                                                     true:  the holder of the private key signed
                                                            exactly this file
                                                     false: anyone else, or one byte changed

    The public key can't make signatures, so it can safely be built into AerOS. That is what
    makes it right for packages fetched over plain HTTP (sys/http.h): whoever controls the
    network or the server in between can change what arrives, but can't make the signature fit.
    AerOS only ever checks signatures; making them is done elsewhere (tools/aersign.py).

    Inside, it uses SHA-512 (the 64 byte relative of SHA-256, in lib/crypto.h), which is offered
    here too. ed25519_selftest checks both against the published test vectors; crypto_selftest
    (lib/crypto.h) runs it at every boot. How it all works is at the top of ed25519.c.
*/
#ifndef ED25519_H
#define ED25519_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#define ED25519_KEY_SIZE       32   // bytes in a public key
#define ED25519_SIGNATURE_SIZE 64   // bytes in a signature
#define SHA512_SIZE            64   // bytes in a SHA-512 digest

// Is `signature` a signature, by the private key that goes with `public_key`, of exactly these
// `length` bytes? False for anything else, including a key or signature that isn't well formed
bool ed25519_verify (const uint8_t signature[ED25519_SIGNATURE_SIZE], const void* message, size_t length,
                     const uint8_t public_key[ED25519_KEY_SIZE]);

// the SHA-512 digest of some bytes (FIPS 180-4)
void sha512 (const void* data, size_t length, uint8_t out[SHA512_SIZE]);

// check SHA-512 and Ed25519 against FIPS 180-4's and RFC 8032's test vectors: true if all match
bool ed25519_selftest (void);

#endif /* ED25519_H */
