/*
    inflate.h - undoing "deflate" compression, the kind inside PNG pictures (and zip and gzip files)

    Deflate squeezes data two ways at once:
    - repeats: a run of bytes seen before is replaced by "go back D bytes and copy L of them"
      (LZ77). "abcabcabc" becomes "abc", then "back 3, copy 6".
    - short codes: bytes and lengths that come up often get codes of fewer bits than rare ones
      (Huffman coding), the way Morse code gives E a single dot.

    The data is a row of blocks, each either stored as it is, coded with a fixed set of codes
    everyone knows, or with codes of its own (sent at the start of the block, themselves
    squeezed the same way). inflate_zlib() undoes all of that.

    PNG wraps the deflate data in "zlib" format: 2 bytes saying it's deflate, the data, then a
    4 byte checksum (which isn't checked here: a PNG that is damaged shows up as a picture
    that won't decode, or looks wrong, and nothing worse).

    The definition of the format, which this follows closely: RFC 1951 (deflate) and RFC 1950
    (zlib). Mark Adler's "puff.c" (in zlib's source) is the clearest short decoder to compare with.
*/
#ifndef INFLATE_H
#define INFLATE_H

#include <stdint.h>
#include <stdbool.h>

// Undo zlib (deflate) compression: `in` is in_length bytes of zlib data, and the result goes in
// `out`, which has room for out_length bytes. The number of bytes written goes in *written.
// False if the data is damaged, isn't zlib, or would need more room than out has
bool inflate_zlib(const uint8_t* in, uint32_t in_length, uint8_t* out, uint32_t out_length, uint32_t* written);

#endif
