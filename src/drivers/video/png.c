/*
    png.c - PNG pictures (see picture.h)

    A PNG file is an 8 byte signature, then "chunks": a length (4 bytes, big endian), a 4 letter
    type, that many bytes of data, and a 4 byte checksum (not checked here). The chunks used:

        IHDR  the size, and how pixels are stored: the bit depth (bits per sample) and the
              colour type (0 grey, 2 RGB, 3 palette, 4 grey + alpha, 6 RGB + alpha)
        PLTE  the palette, for colour type 3: 3 bytes a colour
        tRNS  see-through colours: an alpha for each palette colour, or the one grey or RGB
              colour that is see-through
        IDAT  the pixels, squeezed with deflate. A big picture's are spread over many IDATs,
              which join up into one stream
        IEND  the end

    Inflated, the pixels are rows, each starting with a byte saying how the row was "filtered":
    stored as the difference from something the decoder already has, which squeezes better.

        0 none    1 the pixel to the left    2 the pixel above    3 their average
        4 Paeth: whichever of left, above and above left is closest to left + above - above left

    An interlaced (Adam7) PNG stores the picture as 7 smaller ones, each every 8th or 4th or 2nd
    pixel of the whole, so a slow download can show a rough picture early. Each of the 7 is
    filtered on its own.

    https://www.w3.org/TR/png/
*/

#include "picture.h"
#include "../../lib/inflate.h"
#include "../../lib/aer_stdlib.h"
#include "../../lib/aer_string.h"

// what IHDR says, and the other chunks that matter
typedef struct {
    int width, height;
    int depth;      // bits per sample: 1, 2, 4, 8 or 16
    int colour;     // colour type (see the top of this file)
    int interlaced; // 1 for Adam7
    int channels;   // samples per pixel: 1 grey or palette, 2 grey + alpha, 3 RGB, 4 RGBA
    const uint8_t* palette;
    uint32_t colours;              // in the palette
    const uint8_t* alphas;         // tRNS for a palette: an alpha for each of the first few colours
    uint32_t alpha_count;
    int key_r, key_g, key_b;       // tRNS for grey or RGB: the see-through colour, -1 if none
} png_t;

// where each of the 7 interlace passes starts, and how far apart its pixels are
static const int PASS_X[7] = { 0, 4, 0, 2, 0, 1, 0 };
static const int PASS_Y[7] = { 0, 0, 4, 0, 2, 0, 1 };
static const int PASS_DX[7] = { 8, 8, 4, 4, 2, 2, 1 };
static const int PASS_DY[7] = { 8, 8, 8, 4, 4, 2, 2 };

// a 32 bit big endian number
static uint32_t be32(const uint8_t* p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

// a PNG's size, from its IHDR chunk, which always comes first (see picture.h)
bool png_size(const uint8_t* data, uint32_t length, int* width, int* height) {
    if (length < 33 || memcmp(data + 12, "IHDR", 4) != 0) {
        return false;
    }
    uint32_t w = be32(data + 16), h = be32(data + 20);
    if (w > PICTURE_MAX_SIDE || h > PICTURE_MAX_SIDE) {
        return false;
    }
    *width = (int)w;
    *height = (int)h;
    return picture_size_allowed(*width, *height);
}

// bytes in one row of a picture w pixels wide (not counting its filter byte)
static uint32_t row_bytes(const png_t* p, int w) {
    return ((uint32_t)w * (uint32_t)(p->channels * p->depth) + 7) / 8;
}

// how wide and tall pass n of an interlaced picture is (0 if it has no pixels)
static void pass_size(const png_t* p, int n, int* w, int* h) {
    *w = p->width > PASS_X[n] ? (p->width - PASS_X[n] + PASS_DX[n] - 1) / PASS_DX[n] : 0;
    *h = p->height > PASS_Y[n] ? (p->height - PASS_Y[n] + PASS_DY[n] - 1) / PASS_DY[n] : 0;
}

// the Paeth predictor: whichever of a (left), b (above) and c (above left) is nearest a + b - c
static uint8_t paeth(int a, int b, int c) {
    int p = a + b - c;
    int pa = p > a ? p - a : a - p;
    int pb = p > b ? p - b : b - p;
    int pc = p > c ? p - c : c - p;
    if (pa <= pb && pa <= pc) {
        return (uint8_t)a;
    }
    return (uint8_t)(pb <= pc ? b : c);
}

// Undo the filters of a picture h rows of `bytes` bytes (each after its filter byte), in place.
// `step` is the bytes in a whole pixel (at least 1): "left" means that far back. False if a row
// has a filter number that doesn't exist
static bool unfilter(uint8_t* rows, int h, uint32_t bytes, uint32_t step) {
    uint8_t* above = NULL; // the row above, already unfiltered (none for the first row)
    for (int y = 0; y < h; y++) {
        uint8_t filter = rows[0];
        uint8_t* row = rows + 1;
        for (uint32_t i = 0; i < bytes; i++) {
            int a = i >= step ? row[i - step] : 0;          // left
            int b = above ? above[i] : 0;                    // above
            int c = (above && i >= step) ? above[i - step] : 0; // above left
            switch (filter) {
                case 0: break;
                case 1: row[i] = (uint8_t)(row[i] + a); break;
                case 2: row[i] = (uint8_t)(row[i] + b); break;
                case 3: row[i] = (uint8_t)(row[i] + ((a + b) >> 1)); break;
                case 4: row[i] = (uint8_t)(row[i] + paeth(a, b, c)); break;
                default: return false;
            }
        }
        above = row;
        rows += 1 + bytes;
    }
    return true;
}

// sample number `i` of a row, at the picture's bit depth: 16 bit samples give their top 8 bits,
// and samples smaller than a byte come from the top of the byte down
static uint32_t sample(const png_t* p, const uint8_t* row, uint32_t i) {
    switch (p->depth) {
        case 16: return row[i * 2];
        case 8: return row[i];
        default: {
            uint32_t bit = i * (uint32_t)p->depth;
            uint32_t shift = 8 - (uint32_t)p->depth - (bit & 7);
            return (row[bit / 8] >> shift) & ((1u << p->depth) - 1);
        }
    }
}

// the full 16 bit sample, for comparing with a tRNS colour (which is given at the full depth)
static int full_sample(const png_t* p, const uint8_t* row, uint32_t i) {
    if (p->depth == 16) {
        return (row[i * 2] << 8) | row[i * 2 + 1];
    }
    return (int)sample(p, row, i);
}

// a grey sample of fewer than 8 bits stretched to 0 to 255 (a 1 bit 1 is 255, a 2 bit 1 is 85)
static uint32_t stretch(const png_t* p, uint32_t value) {
    if (p->depth >= 8) {
        return value;
    }
    return value * 255 / ((1u << p->depth) - 1);
}

// pixel x of an unfiltered row, as 0xAARRGGBB
static uint32_t pixel(const png_t* p, const uint8_t* row, int x) {
    uint32_t i = (uint32_t)x * (uint32_t)p->channels; // its first sample
    uint32_t r, g, b, a = 255;
    switch (p->colour) {
        case 0: // grey
            r = g = b = stretch(p, sample(p, row, i));
            if (p->key_r >= 0 && full_sample(p, row, i) == p->key_r) {
                a = 0;
            }
            break;
        case 2: // RGB
            r = sample(p, row, i);
            g = sample(p, row, i + 1);
            b = sample(p, row, i + 2);
            if (p->key_r >= 0 && full_sample(p, row, i) == p->key_r &&
                full_sample(p, row, i + 1) == p->key_g && full_sample(p, row, i + 2) == p->key_b) {
                a = 0;
            }
            break;
        case 3: { // a palette colour
            uint32_t index = sample(p, row, i);
            if (index >= p->colours) {
                return 0xFF000000u; // past the end of the palette: black
            }
            r = p->palette[index * 3];
            g = p->palette[index * 3 + 1];
            b = p->palette[index * 3 + 2];
            if (index < p->alpha_count) {
                a = p->alphas[index];
            }
            break;
        }
        case 4: // grey and alpha
            r = g = b = sample(p, row, i);
            a = sample(p, row, i + 1);
            break;
        default: // 6, RGB and alpha
            r = sample(p, row, i);
            g = sample(p, row, i + 1);
            b = sample(p, row, i + 2);
            a = sample(p, row, i + 3);
            break;
    }
    return (a << 24) | (r << 16) | (g << 8) | b;
}

// Read IHDR and check it describes a PNG this can decode, then find PLTE and tRNS, and add up
// the IDAT chunks' length. False if anything is missing, damaged or not allowed
static bool read_chunks(const uint8_t* data, uint32_t length, png_t* p, uint32_t* idat_length) {
    memset(p, 0, sizeof(*p));
    p->key_r = p->key_g = p->key_b = -1;
    if (!png_size(data, length, &p->width, &p->height)) {
        return false;
    }
    p->depth = data[24];
    p->colour = data[25];
    p->interlaced = data[28];
    // the depths each colour type allows, and compression and filter method 0 (the only ones)
    static const int CHANNELS[7] = { 1, 0, 3, 1, 2, 0, 4 };
    if (p->colour > 6 || CHANNELS[p->colour] == 0 || data[26] != 0 || data[27] != 0 || p->interlaced > 1) {
        return false;
    }
    p->channels = CHANNELS[p->colour];
    int d = p->depth;
    bool depth_ok = (d == 8 || d == 16) || (d < 8 && (d == 1 || d == 2 || d == 4) && (p->colour == 0 || p->colour == 3));
    if (!depth_ok || (p->colour == 3 && d == 16)) {
        return false;
    }

    // the other chunks, one after another until IEND
    *idat_length = 0;
    uint32_t pos = 8;
    while (pos + 12 <= length) {
        uint32_t size = be32(data + pos);
        const uint8_t* type = data + pos + 4;
        const uint8_t* body = data + pos + 8;
        if (size > length - pos - 12) {
            return false; // runs off the end of the file
        }
        if (memcmp(type, "PLTE", 4) == 0) {
            p->palette = body;
            p->colours = size / 3;
        } else if (memcmp(type, "tRNS", 4) == 0) {
            if (p->colour == 3) {
                p->alphas = body;
                p->alpha_count = size;
            } else if (p->colour == 0 && size >= 2) {
                p->key_r = (body[0] << 8) | body[1];
            } else if (p->colour == 2 && size >= 6) {
                p->key_r = (body[0] << 8) | body[1];
                p->key_g = (body[2] << 8) | body[3];
                p->key_b = (body[4] << 8) | body[5];
            }
        } else if (memcmp(type, "IDAT", 4) == 0) {
            *idat_length += size;
        } else if (memcmp(type, "IEND", 4) == 0) {
            break;
        }
        pos += 12 + size;
    }
    return *idat_length > 0 && (p->colour != 3 || p->palette != NULL);
}

// decode a PNG (see picture.h)
bool png_decode(const uint8_t* data, uint32_t length, picture_t* out) {
    png_t p;
    uint32_t idat_length;
    if (!read_chunks(data, length, &p, &idat_length)) {
        return false;
    }

    // join the IDAT chunks' data into one zlib stream
    uint8_t* squeezed = malloc(idat_length);
    if (squeezed == NULL) {
        return false;
    }
    uint32_t joined = 0;
    for (uint32_t pos = 8; pos + 12 <= length; ) {
        uint32_t size = be32(data + pos);
        if (memcmp(data + pos + 4, "IDAT", 4) == 0) {
            memcpy(squeezed + joined, data + pos + 8, size);
            joined += size;
        } else if (memcmp(data + pos + 4, "IEND", 4) == 0) {
            break;
        }
        pos += 12 + size;
    }

    // how many bytes the pixels take once inflated: each pass's rows (just one pass if it
    // isn't interlaced), each with its filter byte
    int passes = p.interlaced ? 7 : 1;
    uint32_t raw_length = 0;
    for (int n = 0; n < passes; n++) {
        int w = p.width, h = p.height;
        if (p.interlaced) {
            pass_size(&p, n, &w, &h);
        }
        if (w > 0 && h > 0) {
            raw_length += (uint32_t)h * (1 + row_bytes(&p, w));
        }
    }
    uint8_t* raw = malloc(raw_length);
    uint32_t written = 0;
    bool ok = raw != NULL && inflate_zlib(squeezed, idat_length, raw, raw_length, &written) &&
              written == raw_length;
    free(squeezed);
    out->pixels = ok ? malloc((uint32_t)p.width * (uint32_t)p.height * 4) : NULL;
    if (out->pixels == NULL) {
        free(raw);
        return false;
    }
    out->width = p.width;
    out->height = p.height;

    // unfilter each pass, then put its pixels where they go in the whole picture
    uint32_t step = (uint32_t)(p.channels * p.depth) / 8;
    if (step == 0) {
        step = 1; // pixels smaller than a byte: "left" is the byte before
    }
    uint8_t* rows = raw;
    for (int n = 0; n < passes; n++) {
        int w = p.width, h = p.height;
        int x0 = 0, y0 = 0, dx = 1, dy = 1;
        if (p.interlaced) {
            pass_size(&p, n, &w, &h);
            x0 = PASS_X[n];
            y0 = PASS_Y[n];
            dx = PASS_DX[n];
            dy = PASS_DY[n];
        }
        if (w == 0 || h == 0) {
            continue;
        }
        uint32_t bytes = row_bytes(&p, w);
        if (!unfilter(rows, h, bytes, step)) {
            free(raw);
            free(out->pixels);
            out->pixels = NULL;
            return false;
        }
        for (int y = 0; y < h; y++) {
            const uint8_t* row = rows + (uint32_t)y * (1 + bytes) + 1;
            uint32_t* dest = out->pixels + (uint32_t)(y0 + y * dy) * (uint32_t)p.width;
            for (int x = 0; x < w; x++) {
                dest[x0 + x * dx] = pixel(&p, row, x);
            }
        }
        rows += (uint32_t)h * (1 + bytes);
    }
    free(raw);
    return true;
}
