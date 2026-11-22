/*
    picture.h - PNG, GIF and JPEG pictures, turned into pixels for gfx.c to draw

    BMP pictures are drawn straight from the file by gfx.c, as their pixels are stored as they
    are. The other kinds are squeezed, each its own way, so they have to be decoded into plain
    pixels first:

        PNG   png.c    pixels squeezed with deflate (lib/inflate.c), a row at a time, each row
                       "filtered" first (stored as the difference from the row above, or the
                       pixel before) because differences squeeze better. Lossless
        GIF   gif.c    up to 256 colours from a palette, the colour numbers squeezed with LZW.
                       Only the first picture of an animated GIF is shown
        JPEG  jpeg.c   photos: the picture cut into 8 x 8 squares, each stored as a mix of
                       smooth waves (a DCT) with the fine detail thrown away. Lossy. Baseline and
                       progressive JPEGs both work; the rare arithmetic coded and lossless kinds don't

    Decoded pixels are 0xAARRGGBB: AA is how solid the pixel is, from 0 (see-through) to 255
    (solid). PNG and GIF pictures can have see-through parts, and gfx_image mixes those with
    what's already on the screen.

    Every decoder checks what it reads against the length of the data, so a damaged or
    deliberately misleading file (pictures come from the web) gives "not a picture" rather than
    reading or writing out of bounds.
*/
#ifndef PICTURE_H
#define PICTURE_H

#include <stdint.h>
#include <stdbool.h>

// the biggest picture decoded: 16384 pixels either way (as for BMP), and 16 million pixels in
// all (64 MB of pixels). A file claiming more is turned down before any memory is used
#define PICTURE_MAX_SIDE 16384
#define PICTURE_MAX_PIXELS (16u * 1024u * 1024u)

// a decoded picture: width x height pixels, a row at a time from the top left, each 0xAARRGGBB
typedef struct {
    int width;
    int height;
    uint32_t* pixels;  // from malloc
} picture_t;

// is this a PNG, GIF or JPEG? Only looks at the first few bytes
bool picture_known(const uint8_t* data, uint32_t length);

// a PNG, GIF or JPEG's size from its headers, without decoding it. False if it isn't one, or
// the headers are damaged
bool picture_size(const uint8_t* data, uint32_t length, int* width, int* height);

// The decoded picture, or NULL if it isn't a PNG, GIF or JPEG that can be decoded (or there
// isn't the memory). The last one decoded is kept, so drawing the same picture again and again
// (a game, or the viewer zooming in) only decodes it once: the picture belongs to picture.c,
// and stays good until the next call
const picture_t* picture_decode(const uint8_t* data, uint32_t length);

// the decoders, for picture.c: each fills in *out (its pixels from malloc) and gives back true,
// or false if the data isn't one it can decode. The _size ones only read the headers
bool png_size(const uint8_t* data, uint32_t length, int* width, int* height);
bool png_decode(const uint8_t* data, uint32_t length, picture_t* out);
bool gif_size(const uint8_t* data, uint32_t length, int* width, int* height);
bool gif_decode(const uint8_t* data, uint32_t length, picture_t* out);
bool jpeg_size(const uint8_t* data, uint32_t length, int* width, int* height);
bool jpeg_decode(const uint8_t* data, uint32_t length, picture_t* out);

// is width x height a size the decoders will make room for? (see PICTURE_MAX_SIDE)
bool picture_size_allowed(int width, int height);

#endif
