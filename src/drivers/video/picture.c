/*
    picture.c - which decoder a picture needs, and the last picture decoded (see picture.h)

    Each kind of picture starts with bytes of its own, so the first few bytes say which it is:

        PNG    137 'P' 'N' 'G' 13 10 26 10
        GIF    'G' 'I' 'F' '8' (then "7a" or "9a")
        JPEG   255 216 255  (the "start of image" marker, then the first of the others)
*/

#include "picture.h"
#include "../../lib/aer_stdlib.h"
#include "../../lib/aer_string.h"

// the last picture decoded, and how to know it's the same data next time
static picture_t last = { 0, 0, NULL };
static uint32_t last_length = 0;
static uint32_t last_hash = 0;

// is it a PNG? Its 8 byte signature
static bool is_png(const uint8_t* d, uint32_t length) {
    static const uint8_t SIGNATURE[8] = { 137, 'P', 'N', 'G', 13, 10, 26, 10 };
    return length >= 8 && memcmp(d, SIGNATURE, 8) == 0;
}

// is it a GIF? "GIF8"
static bool is_gif(const uint8_t* d, uint32_t length) {
    return length >= 6 && d[0] == 'G' && d[1] == 'I' && d[2] == 'F' && d[3] == '8';
}

// is it a JPEG? The start of image marker (FF D8), and the FF of the next marker
static bool is_jpeg(const uint8_t* d, uint32_t length) {
    return length >= 4 && d[0] == 0xFF && d[1] == 0xD8 && d[2] == 0xFF;
}

// is this a PNG, GIF or JPEG? (see picture.h)
bool picture_known(const uint8_t* data, uint32_t length) {
    return data != NULL && (is_png(data, length) || is_gif(data, length) || is_jpeg(data, length));
}

// is width x height a size the decoders will make room for? (see picture.h)
bool picture_size_allowed(int width, int height) {
    // the product is checked after the sides, so it can't wrap round: 16384 x 16384 fits in 32 bits
    return width > 0 && height > 0 && width <= PICTURE_MAX_SIDE && height <= PICTURE_MAX_SIDE &&
           (uint32_t)width * (uint32_t)height <= PICTURE_MAX_PIXELS;
}

// a PNG, GIF or JPEG's size from its headers (see picture.h)
bool picture_size(const uint8_t* data, uint32_t length, int* width, int* height) {
    if (data == NULL) {
        return false;
    }
    if (is_png(data, length)) {
        return png_size(data, length, width, height);
    }
    if (is_gif(data, length)) {
        return gif_size(data, length, width, height);
    }
    if (is_jpeg(data, length)) {
        return jpeg_size(data, length, width, height);
    }
    return false;
}

// A number made from every byte of the data (FNV-1a), to tell whether a picture is the one
// decoded last time. Much quicker than decoding it again: a few milliseconds for a big photo
static uint32_t hash(const uint8_t* data, uint32_t length) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < length; i++) {
        h ^= data[i];
        h *= 16777619u;
    }
    return h;
}

// decode a PNG, GIF or JPEG, or give back the one decoded last time (see picture.h)
const picture_t* picture_decode(const uint8_t* data, uint32_t length) {
    if (!picture_known(data, length)) {
        return NULL;
    }
    uint32_t h = hash(data, length);
    if (last.pixels != NULL && length == last_length && h == last_hash) {
        return &last;
    }

    // a different picture: let the last one go, then decode this one
    free(last.pixels);
    memset(&last, 0, sizeof(last));
    last_length = 0;
    picture_t p = { 0, 0, NULL };
    bool ok;
    if (is_png(data, length)) {
        ok = png_decode(data, length, &p);
    } else if (is_gif(data, length)) {
        ok = gif_decode(data, length, &p);
    } else {
        ok = jpeg_decode(data, length, &p);
    }
    if (!ok) {
        free(p.pixels);
        return NULL;
    }
    last = p;
    last_length = length;
    last_hash = h;
    return &last;
}
