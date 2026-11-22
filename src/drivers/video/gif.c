/*
    gif.c - GIF pictures (see picture.h)

    A GIF file, in order (numbers are little endian):

        "GIF87a" or "GIF89a"
        the screen: width, height (2 bytes each), flags, background colour, aspect
            flags: 0x80 = a global palette follows, with 2^((flags & 7) + 1) colours
        the global palette, 3 bytes (red, green, blue) a colour
        then blocks, each starting with one byte:
            0x21 an extension: a label byte, then "sub-blocks" (a length byte, that many bytes,
                 ending with a length of 0). Label 0xF9 says which colour is see-through
            0x2C a picture: left, top, width, height, flags (0x80 = its own palette follows,
                 0x40 = interlaced), its palette, then the smallest code size and the LZW
                 squeezed colour numbers, in sub-blocks
            0x3B the end

    An animated GIF has many pictures; only the first is decoded here.

    LZW: the colour numbers are squeezed by giving each run of them seen before a code of its
    own. The decoder builds the same table of runs as it reads, so the table is never sent:
    codes below `clear` are single colours, `clear` starts the table again, `clear + 1` is the
    end, and each code after that is "the run before, plus the first colour of this one". Codes
    start (smallest code size + 1) bits long and grow a bit each time the table fills, up to 12.

    https://www.w3.org/Graphics/GIF/spec-gif89a.txt
*/

#include "picture.h"
#include "../../lib/aer_stdlib.h"
#include "../../lib/aer_string.h"

#define LZW_CODES 4096 // 12 bit codes at most

// a 16 bit little endian number
static uint32_t le16(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}

// a GIF's size, from its screen description (see picture.h)
bool gif_size(const uint8_t* data, uint32_t length, int* width, int* height) {
    if (length < 13 || data[0] != 'G' || data[1] != 'I' || data[2] != 'F') {
        return false;
    }
    *width = (int)le16(data + 6);
    *height = (int)le16(data + 8);
    return picture_size_allowed(*width, *height);
}

// Skip a run of sub-blocks starting at *pos, leaving *pos after the 0 that ends them. False if
// they run off the end of the data
static bool skip_sub_blocks(const uint8_t* data, uint32_t length, uint32_t* pos) {
    while (*pos < length) {
        uint32_t size = data[*pos];
        *pos += 1 + size;
        if (size == 0) {
            return true;
        }
    }
    return false;
}

// Join a run of sub-blocks starting at *pos into one buffer (from malloc), so the LZW data
// can be read without stopping every 255 bytes. Its length goes in *joined_length
static uint8_t* join_sub_blocks(const uint8_t* data, uint32_t length, uint32_t* pos, uint32_t* joined_length) {
    // first add up how long they are, then copy them
    uint32_t total = 0;
    uint32_t p = *pos;
    while (p < length && data[p] != 0) {
        total += data[p];
        p += 1 + data[p];
    }
    if (p >= length) {
        return NULL;
    }
    uint8_t* joined = malloc(total ? total : 1);
    if (joined == NULL) {
        return NULL;
    }
    total = 0;
    p = *pos;
    while (data[p] != 0) {
        memcpy(joined + total, data + p + 1, data[p]);
        total += data[p];
        p += 1 + data[p];
    }
    *pos = p + 1;
    *joined_length = total;
    return joined;
}

// Undo the LZW squeezing: fills `indices` (count of them) with colour numbers. Rows the data
// stops short of stay 0. False only if the data is damaged before any of it could be used
static bool lzw(const uint8_t* in, uint32_t in_length, int min_size, uint8_t* indices, uint32_t count) {
    if (min_size < 2 || min_size > 8) {
        return false;
    }
    // the table: each code is a run, kept as the code of the run one shorter (prefix) and the
    // colour added on the end (suffix). first[] is each run's first colour
    static uint16_t prefix[LZW_CODES];
    static uint8_t suffix[LZW_CODES];
    static uint8_t first[LZW_CODES];
    static uint8_t run[LZW_CODES]; // a run, built backwards while following prefix[]

    uint32_t clear = 1u << min_size;
    uint32_t end = clear + 1;
    for (uint32_t c = 0; c < clear; c++) {
        prefix[c] = 0xFFFF;
        suffix[c] = (uint8_t)c;
        first[c] = (uint8_t)c;
    }
    uint32_t size = (uint32_t)min_size + 1; // bits per code now
    uint32_t next = clear + 2;              // the next code the table will give out
    int previous = -1;                      // the code before, -1 just after a clear

    uint32_t bit_buffer = 0, bit_count = 0, in_pos = 0, out = 0;
    while (out < count) {
        // the next code, lowest bits first
        while (bit_count < size) {
            if (in_pos >= in_length) {
                return true; // the data ends early: show what there is
            }
            bit_buffer |= (uint32_t)in[in_pos++] << bit_count;
            bit_count += 8;
        }
        uint32_t code = bit_buffer & ((1u << size) - 1);
        bit_buffer >>= size;
        bit_count -= size;

        if (code == clear) {
            size = (uint32_t)min_size + 1;
            next = clear + 2;
            previous = -1;
            continue;
        }
        if (code == end) {
            break;
        }
        if (previous < 0) {
            // straight after a clear: the code must be a single colour
            if (code >= clear) {
                return out > 0;
            }
            indices[out++] = (uint8_t)code;
            previous = (int)code;
            continue;
        }

        // the run this code stands for. A code the table is just about to give out (next) is
        // the tricky case: it is the previous run plus that run's own first colour
        uint32_t c;
        uint8_t first_colour;
        if (code < next) {
            c = code;
            first_colour = first[code];
        } else if (code == next) {
            c = (uint32_t)previous;
            first_colour = first[previous];
        } else {
            return out > 0; // a code from the future: damaged data
        }
        // follow the prefixes back to the start, then write the run out the right way round
        uint32_t length = 0;
        if (code == next) {
            run[length++] = first_colour;
        }
        while (c != 0xFFFF && length < LZW_CODES) {
            run[length++] = suffix[c];
            c = prefix[c];
        }
        while (length > 0 && out < count) {
            indices[out++] = run[--length];
        }

        // the table gets a new run: the previous one plus this one's first colour
        if (next < LZW_CODES) {
            prefix[next] = (uint16_t)previous;
            suffix[next] = first_colour;
            first[next] = first[previous];
            next++;
            // when the codes no longer fit, they get a bit longer (up to 12)
            if (next == (1u << size) && size < 12) {
                size++;
            }
        }
        previous = (int)code;
    }
    return true;
}

// the row of an interlaced GIF that the n-th row in the file goes to: every 8th row from 0,
// then every 8th from 4, every 4th from 2, and the rest (every 2nd from 1)
static int interlaced_row(int n, int height) {
    int pass_rows = (height + 7) / 8; // rows 0, 8, 16...
    if (n < pass_rows) {
        return n * 8;
    }
    n -= pass_rows;
    pass_rows = (height + 3) / 8; // rows 4, 12, 20...
    if (n < pass_rows) {
        return 4 + n * 8;
    }
    n -= pass_rows;
    pass_rows = (height + 1) / 4; // rows 2, 6, 10...
    if (n < pass_rows) {
        return 2 + n * 4;
    }
    n -= pass_rows;
    return 1 + n * 2; // rows 1, 3, 5...
}

// decode a GIF's first picture (see picture.h)
bool gif_decode(const uint8_t* data, uint32_t length, picture_t* out) {
    int width, height;
    if (!gif_size(data, length, &width, &height)) {
        return false;
    }
    // the global palette, if there is one
    uint32_t pos = 13;
    const uint8_t* palette = NULL;
    uint32_t colours = 0;
    if (data[10] & 0x80) {
        colours = 1u << ((data[10] & 7) + 1);
        palette = data + pos;
        pos += colours * 3;
        if (pos > length) {
            return false;
        }
    }

    int see_through = -1; // the see-through colour number from a 0xF9 extension, -1 if none
    while (pos < length) {
        uint8_t block = data[pos++];
        if (block == 0x21) {
            // an extension: the only one that matters here says which colour is see-through
            if (pos >= length) {
                return false;
            }
            uint8_t label = data[pos++];
            if (label == 0xF9 && pos + 5 <= length && data[pos] == 4) {
                // its 4 bytes: flags (0x01 = a colour is see-through), a delay, the colour
                if (data[pos + 1] & 0x01) {
                    see_through = data[pos + 4];
                }
            }
            if (!skip_sub_blocks(data, length, &pos)) {
                return false;
            }
        } else if (block == 0x2C) {
            // the first picture: where it is on the screen, its size, and its own palette
            if (pos + 9 > length) {
                return false;
            }
            int left = (int)le16(data + pos);
            int top = (int)le16(data + pos + 2);
            int w = (int)le16(data + pos + 4);
            int h = (int)le16(data + pos + 6);
            uint8_t flags = data[pos + 8];
            pos += 9;
            if (flags & 0x80) {
                colours = 1u << ((flags & 7) + 1);
                palette = data + pos;
                pos += colours * 3;
            }
            if (palette == NULL || pos + 1 > length || !picture_size_allowed(w, h)) {
                return false;
            }
            int min_size = data[pos++];
            uint32_t squeezed_length;
            uint8_t* squeezed = join_sub_blocks(data, length, &pos, &squeezed_length);
            uint8_t* indices = calloc((uint32_t)w * (uint32_t)h, 1);
            out->pixels = calloc((uint32_t)width * (uint32_t)height, 4); // all see-through to start
            bool ok = squeezed != NULL && indices != NULL && out->pixels != NULL &&
                      lzw(squeezed, squeezed_length, min_size, indices, (uint32_t)w * (uint32_t)h);
            free(squeezed);
            if (!ok) {
                free(indices);
                return false;
            }
            out->width = width;
            out->height = height;

            // colour in the picture where it goes on the screen (parts off the screen are left out)
            for (int row = 0; row < h; row++) {
                int y = top + ((flags & 0x40) ? interlaced_row(row, h) : row);
                if (y >= height) {
                    continue;
                }
                for (int col = 0; col < w; col++) {
                    int x = left + col;
                    uint8_t index = indices[(uint32_t)row * (uint32_t)w + (uint32_t)col];
                    if (x >= width || (int)index == see_through || index >= colours) {
                        continue;
                    }
                    const uint8_t* c = palette + index * 3;
                    out->pixels[(uint32_t)y * (uint32_t)width + (uint32_t)x] =
                        0xFF000000u | ((uint32_t)c[0] << 16) | ((uint32_t)c[1] << 8) | c[2];
                }
            }
            free(indices);
            return true;
        } else {
            // the end (0x3B) before any picture, or something that isn't a GIF block
            return false;
        }
    }
    return false;
}
