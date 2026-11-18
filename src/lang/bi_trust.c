/*
    bi_trust.c
    AerLang's built-in functions for deciding whether to trust something before using it:

        verify(data, signature, key)   was data signed by the holder of key's private key?
        packagekey()                   the public key AerOS trusts packages from
        scan(source)                   what a program could do, read before it runs

    Together they are what a package installer needs: fetch a package and its signature
    (httpget), check the signature against packagekey() with verify, show what the program in it
    could do with scan, and only then ask whether to install it. Keys and signatures are written
    as hex, so they fit in text files (an index of packages, say).

    How a built-in works, and how to add one, is at the top of builtins.h.
*/

#include "builtins.h"
#include "aerlang.h"
#include "../lib/ed25519.h"
#include "../lib/crypto.h"
#include "../lib/aer_string.h"
#include "../sys/package_key.h"

// verify(data, signature, key): was `data` (text or bytes) signed by the private key that goes
// with `key`? signature (128 hex digits) and key (64 hex digits) are hex text. True only if the
// signature checks out: anything else, a malformed signature or key included, is false
value_t bi_verify (int argc, value_t* argv, int line) {
    (void)argc;
    if (argv[0].type != V_STR && argv[0].type != V_BYTES) {
        aer_error(line, "verify() checks text or bytes, not a %s", aer_type_name(argv[0]));
    }
    const char* signature_hex = str_arg(argv, 1, line, "verify")->data.chars;
    const char* key_hex = str_arg(argv, 2, line, "verify")->data.chars;

    // the hex must be exactly the right length, all of it hex digits
    uint8_t signature[ED25519_SIGNATURE_SIZE];
    uint8_t key[ED25519_KEY_SIZE];
    bool well_formed = slen(signature_hex) == ED25519_SIGNATURE_SIZE * 2 && slen(key_hex) == ED25519_KEY_SIZE * 2
                       && crypto_from_hex(signature_hex, signature, sizeof(signature))
                       && crypto_from_hex(key_hex, key, sizeof(key));
    if (!well_formed) {
        return aer_bool(false);
    }
    const aer_object_t* data = argv[0].as.obj;
    return aer_bool(ed25519_verify(signature, data->data.chars, data->length, key));
}

// packagekey(): the public key packages are checked against (sys/package_key.h), as 64 hex
// digits, or "" if it hasn't been set yet (then verify() with it is always false)
value_t bi_package_key (int argc, value_t* argv, int line) {
    (void)argc;
    (void)argv;
    (void)line;
    return aer_string(AEROS_PACKAGE_KEY, (uint32_t)slen(AEROS_PACKAGE_KEY));
}

// a list of text values, from rows of a C table
static value_t text_list (char rows[][100], int count) {
    value_t list = aer_list((uint32_t)count);
    for (int i = 0; i < count; i++) {
        const char* row = rows[i];
        aer_list_push(list.as.obj, aer_string(row, (uint32_t)slen(row)));
    }
    return list;
}

// scan(source): what the program `source` (its text) could do, found by reading it, without
// running it (lang/aer_scan.c, the same as the SCAN command). A list of two lists:
//     [0]  what it can do, each "delete files (delf): asks you first"
//     [1]  what is worth a closer look, each "line 5: it asks for a password: ..." (often empty)
value_t bi_scan (int argc, value_t* argv, int line) {
    (void)argc;
    const char* source = str_arg(argv, 0, line, "scan")->data.chars;
    // too big for the stack (about 3 KB), and only one scan runs at a time
    static aer_scan_result_t result;
    aer_scan_read(source, &result);

    // each ability, with what keeps it in check after a colon
    static char abilities[AER_SCAN_ABILITIES_MAX][100];
    for (int i = 0; i < result.ability_count; i++) {
        scpy(abilities[i], result.abilities[i], sizeof(abilities[i]));
        if (result.kept_in_check[i][0] != '\0') {
            scat(abilities[i], ": ", sizeof(abilities[i]));
            scat(abilities[i], result.kept_in_check[i], sizeof(abilities[i]));
        }
    }
    value_t both = aer_list(2);
    aer_list_push(both.as.obj, text_list(abilities, result.ability_count));
    aer_list_push(both.as.obj, text_list(result.hints, result.hint_count));
    return both;
}
