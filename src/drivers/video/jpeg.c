/*
    jpeg.c - JPEG pictures (see picture.h)

    How a JPEG is made, which this undoes from the bottom up:
    1. The colours are turned from red, green, blue into brightness (Y) and two colour
       differences (Cb, Cr). Eyes notice detail in brightness far more than in colour, so the
       colour planes are usually kept at half the width and height ("subsampling").
    2. Each plane is cut into 8 x 8 squares ("blocks"), and each block is turned into 64
       numbers saying how much of each of 64 patterns of waves it is made of (the DCT). The
       first number (DC) is the block's average; the rest (AC) are finer and finer detail.
    3. Each number is divided by a step from a "quantization table" and rounded. Fine detail
       gets big steps, so most of those numbers become 0. This is where the picture loses detail.
    4. The 64 numbers are listed in a zigzag from the coarsest to the finest, so the 0s bunch
       up at the end, and Huffman coded: a run of 0s and the size of the next number share one
       code. Each block's DC is sent as the difference from the block before.

    A "baseline" JPEG sends each block whole, one after another. A "progressive" one sends every
    block's DC first, then the coarser detail of every block, then finer detail, and the top
    bits of numbers before their lower bits, in several "scans", so a slow download sharpens as
    it comes. Here both kinds keep every block's numbers until the end, then turn them into pixels.

    The file is a row of "markers", FF and a byte saying which:

        D8 start of image           C4 Huffman tables (DHT)    DB quantization tables (DQT)
        C0 / C1 baseline frame      C2 progressive frame       DD restart interval (DRI)
        DA a scan (SOS): which planes and which part of the numbers, then the coded data
        D0 to D7 restart markers, inside the coded data      D9 end of image
        E0 to EF, FE: extras (JFIF, Exif, comments), skipped. E14 "Adobe" can say the planes are RGB

    Inside coded data, a byte FF is sent as FF 00, so an FF followed by anything else is a marker.

    The DCT is undone with whole numbers only (the way libjpeg's "jidctint" and stb_image do),
    so it needs no floating point.

    https://www.w3.org/Graphics/JPEG/itu-t81.pdf (the standard, ITU T.81)
*/

#include "picture.h"
#include "../../lib/aer_stdlib.h"
#include "../../lib/aer_string.h"

#define MAX_COMPONENTS 3

// the order the 64 numbers of a block come in: zigzag n is at ZIGZAG[n] in the 8 x 8 block
static const uint8_t ZIGZAG[64] = {
     0,  1,  8, 16,  9,  2,  3, 10, 17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63
};

// a Huffman table: how many codes of each length 1 to 16, and the values in code order. The
// codes of each length are consecutive numbers (as in deflate, see lib/inflate.c)
typedef struct {
    uint8_t count[17];
    uint8_t values[256];
    bool defined;
} huffman_t;

// one plane (Y, Cb or Cr): its sampling, its tables, and the numbers of all its blocks
typedef struct {
    int id;
    int h, v;               // how many blocks across and down it has in each MCU (see below)
    int quant;              // which quantization table
    int dc_table, ac_table; // which Huffman tables, in the scan going on now
    int blocks_w, blocks_h; // blocks across and down, rounded up to whole MCUs
    int16_t* coefficients;  // 64 numbers a block, blocks_w x blocks_h blocks, row by row
    int dc_before;          // the last block's DC, as each DC is sent as a difference
    uint8_t* plane;         // its pixels, once decoded (blocks_w * 8 wide)
} component_t;

// where decoding is up to
typedef struct {
    const uint8_t* data;
    uint32_t length;
    uint32_t pos;              // the next byte to read
    int width, height;
    bool progressive;
    bool rgb;                  // the planes are red, green, blue rather than Y, Cb, Cr
    uint16_t quant[4][64];     // quantization tables, in zigzag order
    huffman_t dc[4], ac[4];
    component_t comp[MAX_COMPONENTS];
    int components;
    int h_max, v_max;          // the biggest h and v: an MCU is h_max x v_max blocks of the
                               // most detailed plane (usually Y), 8 * h_max pixels across
    int mcus_w, mcus_h;        // MCUs across and down the picture
    int restart_interval;      // MCUs between restart markers, 0 for none
    // reading the coded data, a bit at a time
    uint32_t bit_buffer;       // bits waiting, the next one at the top (bit 31)
    int bit_count;
    bool hit_marker;           // the coded data has ended (a marker came): only 0s from now
    int eob_run;               // progressive: how many more blocks have nothing in this scan
    // the scan going on now
    int scan_comps[MAX_COMPONENTS];
    int scan_count;
    int ss, se, ah, al;        // which numbers (ss to se, in zigzag order) and which bits
} jpeg_t;

// a 16 bit big endian number
static int be16(const uint8_t* p) {
    return (p[0] << 8) | p[1];
}

// ---- reading the coded data ---------------------------------------------------------------------

// top the bit buffer up to at least 25 bits, a byte at a time, stopping at a marker (0s after it)
static void fill(jpeg_t* j) {
    while (j->bit_count <= 24) {
        uint32_t byte = 0;
        if (!j->hit_marker && j->pos < j->length) {
            byte = j->data[j->pos];
            if (byte == 0xFF) {
                // FF 00 is a real FF; FF and anything else is a marker: the data has ended
                uint8_t next = j->pos + 1 < j->length ? j->data[j->pos + 1] : 0;
                if (next == 0x00) {
                    j->pos += 2;
                } else {
                    j->hit_marker = true;
                    byte = 0;
                }
            } else {
                j->pos++;
            }
        }
        j->bit_buffer |= byte << (24 - j->bit_count);
        j->bit_count += 8;
    }
}

// the next n bits (up to 16), the first one read being the highest
static int get_bits(jpeg_t* j, int n) {
    if (n == 0) {
        return 0;
    }
    fill(j);
    int value = (int)(j->bit_buffer >> (32 - n));
    j->bit_buffer <<= n;
    j->bit_count -= n;
    return value;
}

// one bit
static int get_bit(jpeg_t* j) {
    return get_bits(j, 1);
}

// Read n bits as a number in JPEG's own way: n bits starting with a 1 are that number, and
// starting with a 0 they are negative. With 3 bits: 100 to 111 are 4 to 7, 000 to 011 are -7 to -4
static int receive(jpeg_t* j, int n) {
    if (n == 0) {
        return 0;
    }
    int value = get_bits(j, n);
    if (value < (1 << (n - 1))) {
        value += 1 - (1 << n);
    }
    return value;
}

// read one value with a Huffman table, a bit at a time. -1 for a code it doesn't have
static int decode(jpeg_t* j, const huffman_t* h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len <= 16; len++) {
        code |= get_bit(j);
        int count = h->count[len];
        if (code - first < count) {
            return h->values[index + code - first];
        }
        index += count;
        first = (first + count) << 1;
        code <<= 1;
    }
    return -1;
}

// at a restart marker: forget the bits left over, step past the marker, and start the DCs again
static void restart(jpeg_t* j) {
    j->bit_buffer = 0;
    j->bit_count = 0;
    j->hit_marker = false;
    // find the marker (there can be fill bytes, FF FF..., before it), and step past it if it
    // is a restart marker
    while (j->pos + 1 < j->length && !(j->data[j->pos] == 0xFF && j->data[j->pos + 1] != 0xFF && j->data[j->pos + 1] != 0)) {
        j->pos++;
    }
    if (j->pos + 1 < j->length && j->data[j->pos + 1] >= 0xD0 && j->data[j->pos + 1] <= 0xD7) {
        j->pos += 2;
    }
    for (int c = 0; c < j->components; c++) {
        j->comp[c].dc_before = 0;
    }
    j->eob_run = 0;
}

// ---- the four ways a block's numbers can come -------------------------------------------------

// baseline: the whole block, DC then every AC, in one go
static bool block_baseline(jpeg_t* j, component_t* c, int16_t* block) {
    int t = decode(j, &j->dc[c->dc_table]);
    if (t < 0 || t > 16) {
        return false;
    }
    c->dc_before += receive(j, t);
    block[0] = (int16_t)c->dc_before;
    // each AC code is a run of 0s (top 4 bits) and the size of the number after them
    for (int k = 1; k < 64; ) {
        int rs = decode(j, &j->ac[c->ac_table]);
        if (rs < 0) {
            return false;
        }
        int run = rs >> 4, size = rs & 15;
        if (size == 0) {
            if (run != 15) {
                break; // end of block: the rest are 0
            }
            k += 16;   // sixteen 0s
            continue;
        }
        k += run;
        if (k > 63) {
            return false;
        }
        block[ZIGZAG[k++]] = (int16_t)receive(j, size);
    }
    return true;
}

// progressive, DC: the first scan sends the top bits of each DC (shifted up by al), later ones
// one more bit each
static bool block_dc(jpeg_t* j, component_t* c, int16_t* block) {
    if (j->ah == 0) {
        int t = decode(j, &j->dc[c->dc_table]);
        if (t < 0 || t > 16) {
            return false;
        }
        c->dc_before += receive(j, t);
        block[0] = (int16_t)(c->dc_before * (1 << j->al));
    } else if (get_bit(j)) {
        block[0] = (int16_t)(block[0] | (1 << j->al));
    }
    return true;
}

// progressive, AC, first time: numbers ss to se, shifted up by al. An "end of band" run says how
// many blocks in a row have nothing more in this scan
static bool block_ac_first(jpeg_t* j, component_t* c, int16_t* block) {
    if (j->eob_run > 0) {
        j->eob_run--;
        return true;
    }
    for (int k = j->ss; k <= j->se; ) {
        int rs = decode(j, &j->ac[c->ac_table]);
        if (rs < 0) {
            return false;
        }
        int run = rs >> 4, size = rs & 15;
        if (size == 0) {
            if (run < 15) {
                // this block and (2^run - 1 + some bits) more have nothing more in this scan
                j->eob_run = (1 << run) - 1;
                if (run) {
                    j->eob_run += get_bits(j, run);
                }
                break;
            }
            k += 16;
            continue;
        }
        k += run;
        if (k > 63) {
            return false;
        }
        block[ZIGZAG[k++]] = (int16_t)(receive(j, size) * (1 << j->al));
    }
    return true;
}

// one more bit of a number already sent: added in the direction of its sign
static void refine(jpeg_t* j, int16_t* number) {
    int bit = 1 << j->al;
    if (get_bit(j) && (*number & bit) == 0) {
        *number = (int16_t)(*number >= 0 ? *number + bit : *number - bit);
    }
}

// Progressive, AC, refining: one more bit of every number already sent, and new numbers (1 or
// -1 at this bit) where there were 0s. The trickiest part of JPEG: this follows libjpeg's
// decode_mcu_AC_refine closely
static bool block_ac_refine(jpeg_t* j, component_t* c, int16_t* block) {
    int k = j->ss;
    if (j->eob_run <= 0) {
        for (; k <= j->se; k++) {
            int rs = decode(j, &j->ac[c->ac_table]);
            if (rs < 0) {
                return false;
            }
            int run = rs >> 4, size = rs & 15;
            int value = 0;
            if (size != 0) {
                // a new number, 1 or -1 at this bit (its size is always 1)
                value = get_bit(j) ? (1 << j->al) : -(1 << j->al);
            } else if (run != 15) {
                // end of band: this block's remaining numbers are refined below, then the run
                j->eob_run = 1 << run;
                if (run) {
                    j->eob_run += get_bits(j, run);
                }
                break;
            }
            // skip `run` 0s, refining the numbers that aren't 0 along the way, and put the new
            // number at the next 0 after them
            while (k <= j->se) {
                int16_t* number = &block[ZIGZAG[k]];
                if (*number != 0) {
                    refine(j, number);
                } else {
                    if (run == 0) {
                        break;
                    }
                    run--;
                }
                k++;
            }
            if (value != 0 && k <= j->se) {
                block[ZIGZAG[k]] = (int16_t)value;
            }
        }
    }
    if (j->eob_run > 0) {
        // in an end of band run: the numbers already there each get one more bit
        for (; k <= j->se; k++) {
            if (block[ZIGZAG[k]] != 0) {
                refine(j, &block[ZIGZAG[k]]);
            }
        }
        j->eob_run--;
    }
    return true;
}

// read one block of a scan, whichever kind of scan it is
static bool read_block(jpeg_t* j, component_t* c, int bx, int by) {
    if (bx >= c->blocks_w || by >= c->blocks_h) {
        return true;
    }
    int16_t* block = c->coefficients + ((uint32_t)by * (uint32_t)c->blocks_w + (uint32_t)bx) * 64;
    if (!j->progressive) {
        return block_baseline(j, c, block);
    }
    if (j->ss == 0) {
        return block_dc(j, c, block);
    }
    return j->ah == 0 ? block_ac_first(j, c, block) : block_ac_refine(j, c, block);
}

// Decode a scan's coded data. With one plane, its blocks come one after another, row by row
// (only as many as the plane really has, not rounded up to MCUs); with several, an MCU at a
// time: each plane's h x v blocks in turn
static bool read_scan(jpeg_t* j) {
    j->bit_buffer = 0;
    j->bit_count = 0;
    j->hit_marker = false;
    j->eob_run = 0;
    for (int c = 0; c < j->components; c++) {
        j->comp[c].dc_before = 0;
    }
    int done = 0; // MCUs (or blocks, with one plane) since the last restart marker

    if (j->scan_count == 1) {
        component_t* c = &j->comp[j->scan_comps[0]];
        // the blocks this plane really has: its size in pixels, rounded up to whole blocks
        int w = ((j->width * c->h + j->h_max - 1) / j->h_max + 7) / 8;
        int h = ((j->height * c->v + j->v_max - 1) / j->v_max + 7) / 8;
        for (int by = 0; by < h; by++) {
            for (int bx = 0; bx < w; bx++) {
                if (j->restart_interval && done == j->restart_interval) {
                    restart(j);
                    done = 0;
                }
                if (!read_block(j, c, bx, by)) {
                    return false;
                }
                done++;
            }
        }
        return true;
    }
    for (int my = 0; my < j->mcus_h; my++) {
        for (int mx = 0; mx < j->mcus_w; mx++) {
            if (j->restart_interval && done == j->restart_interval) {
                restart(j);
                done = 0;
            }
            for (int s = 0; s < j->scan_count; s++) {
                component_t* c = &j->comp[j->scan_comps[s]];
                for (int v = 0; v < c->v; v++) {
                    for (int h = 0; h < c->h; h++) {
                        if (!read_block(j, c, mx * c->h + h, my * c->v + v)) {
                            return false;
                        }
                    }
                }
            }
            done++;
        }
    }
    return true;
}

// ---- the markers ------------------------------------------------------------------------------

// DQT: one or more quantization tables, 8 or 16 bits a step
static bool read_dqt(jpeg_t* j, const uint8_t* p, int length) {
    while (length > 0) {
        int precision = p[0] >> 4, id = p[0] & 15;
        int size = precision ? 129 : 65;
        if (id > 3 || length < size) {
            return false;
        }
        for (int i = 0; i < 64; i++) {
            j->quant[id][i] = (uint16_t)(precision ? be16(p + 1 + i * 2) : p[1 + i]);
        }
        p += size;
        length -= size;
    }
    return true;
}

// DHT: one or more Huffman tables: DC or AC and a number, 16 counts, then the values
static bool read_dht(jpeg_t* j, const uint8_t* p, int length) {
    while (length >= 17) {
        int kind = p[0] >> 4, id = p[0] & 15;
        if (kind > 1 || id > 3) {
            return false;
        }
        huffman_t* h = kind ? &j->ac[id] : &j->dc[id];
        int total = 0;
        h->count[0] = 0;
        for (int i = 1; i <= 16; i++) {
            h->count[i] = p[i];
            total += p[i];
        }
        if (total > 256 || length < 17 + total) {
            return false;
        }
        memcpy(h->values, p + 17, (uint32_t)total);
        h->defined = true;
        p += 17 + total;
        length -= 17 + total;
    }
    return length == 0;
}

// SOF: the picture's size and its planes. Makes room for every block's numbers
static bool read_sof(jpeg_t* j, const uint8_t* p, int length) {
    if (length < 6 || p[0] != 8) {
        return false; // 8 bits a sample is the only kind used for pictures in practice
    }
    j->height = be16(p + 1);
    j->width = be16(p + 3);
    j->components = p[5];
    if (!picture_size_allowed(j->width, j->height) || (j->components != 1 && j->components != 3) ||
        length < 6 + j->components * 3) {
        return false;
    }
    j->h_max = j->v_max = 1;
    for (int c = 0; c < j->components; c++) {
        component_t* comp = &j->comp[c];
        comp->id = p[6 + c * 3];
        comp->h = p[7 + c * 3] >> 4;
        comp->v = p[7 + c * 3] & 15;
        comp->quant = p[8 + c * 3];
        if (comp->h < 1 || comp->h > 4 || comp->v < 1 || comp->v > 4 || comp->quant > 3) {
            return false;
        }
        if (comp->h > j->h_max) j->h_max = comp->h;
        if (comp->v > j->v_max) j->v_max = comp->v;
    }
    // an MCU is 8 * h_max x 8 * v_max pixels; each plane has h x v blocks in each
    j->mcus_w = (j->width + 8 * j->h_max - 1) / (8 * j->h_max);
    j->mcus_h = (j->height + 8 * j->v_max - 1) / (8 * j->v_max);
    for (int c = 0; c < j->components; c++) {
        component_t* comp = &j->comp[c];
        comp->blocks_w = j->mcus_w * comp->h;
        comp->blocks_h = j->mcus_h * comp->v;
        comp->coefficients = calloc((uint32_t)comp->blocks_w * (uint32_t)comp->blocks_h, 64 * 2);
        if (comp->coefficients == NULL) {
            return false;
        }
    }
    // three planes named R, G and B are RGB (Adobe's APP14 marker can say so too)
    if (j->components == 3 && j->comp[0].id == 'R' && j->comp[1].id == 'G' && j->comp[2].id == 'B') {
        j->rgb = true;
    }
    return true;
}

// SOS: which planes this scan has, their Huffman tables, and which numbers and bits it sends
static bool read_sos(jpeg_t* j, const uint8_t* p, int length) {
    if (length < 1) {
        return false;
    }
    j->scan_count = p[0];
    if (j->scan_count < 1 || j->scan_count > j->components || length < 4 + j->scan_count * 2) {
        return false;
    }
    for (int s = 0; s < j->scan_count; s++) {
        int id = p[1 + s * 2], tables = p[2 + s * 2];
        int found = -1;
        for (int c = 0; c < j->components; c++) {
            if (j->comp[c].id == id) {
                found = c;
            }
        }
        if (found < 0 || (tables >> 4) > 3 || (tables & 15) > 3) {
            return false;
        }
        j->scan_comps[s] = found;
        j->comp[found].dc_table = tables >> 4;
        j->comp[found].ac_table = tables & 15;
    }
    const uint8_t* q = p + 1 + j->scan_count * 2;
    j->ss = q[0];
    j->se = q[1];
    j->ah = q[2] >> 4;
    j->al = q[2] & 15;
    if (!j->progressive) {
        j->ss = 0;
        j->se = 63;
        j->ah = j->al = 0;
    } else if (j->ss > j->se || j->se > 63 || (j->ss == 0 && j->se != 0) || (j->ss > 0 && j->scan_count != 1)) {
        return false; // DC and AC are never mixed in a progressive scan, and AC scans have one plane
    }
    // the tables the scan needs must have been sent
    for (int s = 0; s < j->scan_count; s++) {
        component_t* c = &j->comp[j->scan_comps[s]];
        bool needs_dc = j->ss == 0 && j->ah == 0;
        bool needs_ac = j->se > 0 && !(j->progressive && j->ss == 0);
        if ((needs_dc && !j->dc[c->dc_table].defined) || (needs_ac && !j->ac[c->ac_table].defined)) {
            return false;
        }
    }
    return true;
}

// ---- turning the numbers into pixels ------------------------------------------------------------

// keep a pixel value within 0 to 255
static uint8_t clamp(int x) {
    return (uint8_t)(x < 0 ? 0 : x > 255 ? 255 : x);
}

// The DCT undone along 8 numbers (s0 to s7, every `step` apart in `in`), with whole numbers: the
// constants are the DCT's cosines (and sums of them) times 4096. It gives back two halves, x
// and t, which the caller adds and takes away: output n is x[n] + t[3 - n], and output 7 - n
// is x[n] - t[3 - n]
static void idct_1d(const int* in, int step, int* x, int* t) {
    int s0 = in[0], s1 = in[step], s2 = in[2 * step], s3 = in[3 * step];
    int s4 = in[4 * step], s5 = in[5 * step], s6 = in[6 * step], s7 = in[7 * step];
    // the even half
    int p1 = (s2 + s6) * 2217;
    int t2 = p1 + s6 * -7567;
    int t3 = p1 + s2 * 3135;
    int t0 = (s0 + s4) * 4096;
    int t1 = (s0 - s4) * 4096;
    x[0] = t0 + t3;
    x[3] = t0 - t3;
    x[1] = t1 + t2;
    x[2] = t1 - t2;
    // the odd half
    int a0 = s7, a1 = s5, a2 = s3, a3 = s1;
    int p3 = a0 + a2, p4 = a1 + a3;
    int q1 = a0 + a3, q2 = a1 + a2;
    int p5 = (p3 + p4) * 4816;
    a0 *= 1223;
    a1 *= 8410;
    a2 *= 12586;
    a3 *= 6149;
    q1 = p5 + q1 * -3685;
    q2 = p5 + q2 * -10497;
    p3 *= -8034;
    p4 *= -1597;
    t[3] = a3 + q1 + p4;
    t[2] = a2 + q2 + p3;
    t[1] = a1 + q2 + p4;
    t[0] = a0 + q1 + p3;
}

// Undo the DCT of one block (its numbers already multiplied by their quantization steps, in
// normal order), writing 8 x 8 pixels to `out`, `stride` bytes from one row to the next.
// Columns first (keeping 2 more bits than needed, for accuracy), then rows
static void idct_block(const int* numbers, uint8_t* out, int stride) {
    int middle[64];
    int x[4], t[4];
    for (int col = 0; col < 8; col++) {
        idct_1d(numbers + col, 8, x, t);
        for (int n = 0; n < 4; n++) {
            x[n] += 512; // rounding, before dropping 10 bits
        }
        middle[col]      = (x[0] + t[3]) >> 10;
        middle[col + 56] = (x[0] - t[3]) >> 10;
        middle[col + 8]  = (x[1] + t[2]) >> 10;
        middle[col + 48] = (x[1] - t[2]) >> 10;
        middle[col + 16] = (x[2] + t[1]) >> 10;
        middle[col + 40] = (x[2] - t[1]) >> 10;
        middle[col + 24] = (x[3] + t[0]) >> 10;
        middle[col + 32] = (x[3] - t[0]) >> 10;
    }
    for (int row = 0; row < 8; row++) {
        idct_1d(middle + row * 8, 1, x, t);
        // rounding, and adding 128 (pixels were stored around 0, from -128 to 127)
        for (int n = 0; n < 4; n++) {
            x[n] += 65536 + (128 << 17);
        }
        uint8_t* o = out + row * stride;
        o[0] = clamp((x[0] + t[3]) >> 17);
        o[7] = clamp((x[0] - t[3]) >> 17);
        o[1] = clamp((x[1] + t[2]) >> 17);
        o[6] = clamp((x[1] - t[2]) >> 17);
        o[2] = clamp((x[2] + t[1]) >> 17);
        o[5] = clamp((x[2] - t[1]) >> 17);
        o[3] = clamp((x[3] + t[0]) >> 17);
        o[4] = clamp((x[3] - t[0]) >> 17);
    }
}

// turn every block of every plane into pixels: multiply by the quantization steps, then undo the DCT
static bool make_planes(jpeg_t* j) {
    for (int c = 0; c < j->components; c++) {
        component_t* comp = &j->comp[c];
        int stride = comp->blocks_w * 8;
        comp->plane = malloc((uint32_t)stride * (uint32_t)comp->blocks_h * 8);
        if (comp->plane == NULL) {
            return false;
        }
        const uint16_t* q = j->quant[comp->quant];
        int numbers[64];
        for (int by = 0; by < comp->blocks_h; by++) {
            for (int bx = 0; bx < comp->blocks_w; bx++) {
                const int16_t* block = comp->coefficients + ((uint32_t)by * (uint32_t)comp->blocks_w + (uint32_t)bx) * 64;
                // the table is in zigzag order, the block's numbers in normal order. Kept within
                // 16 bits, as a damaged file could otherwise make the sums in idct_1d overflow
                for (int k = 0; k < 64; k++) {
                    int n = block[ZIGZAG[k]] * q[k];
                    numbers[ZIGZAG[k]] = n < -32768 ? -32768 : n > 32767 ? 32767 : n;
                }
                idct_block(numbers, comp->plane + (uint32_t)by * 8 * (uint32_t)stride + (uint32_t)bx * 8, stride);
            }
        }
    }
    return true;
}

// Put the planes together into 0xAARRGGBB pixels. A plane with fewer pixels (colour, usually)
// is stretched to the full size: each of its pixels is used for 2 (or more) of the picture's.
// Y, Cb, Cr become red, green, blue with the JFIF sums, in whole numbers (times 65536), Cb and
// Cr being differences from 128:
//     R = Y + 1.402 Cr      G = Y - 0.344136 Cb - 0.714136 Cr      B = Y + 1.772 Cb
static void make_pixels(const jpeg_t* j, uint32_t* out) {
    for (int y = 0; y < j->height; y++) {
        // the row of each plane this row of pixels comes from
        const uint8_t* rows[MAX_COMPONENTS];
        for (int c = 0; c < j->components; c++) {
            const component_t* comp = &j->comp[c];
            rows[c] = comp->plane + (uint32_t)(y * comp->v / j->v_max) * (uint32_t)(comp->blocks_w * 8);
        }
        uint32_t* dest = out + (uint32_t)y * (uint32_t)j->width;
        for (int x = 0; x < j->width; x++) {
            int s[MAX_COMPONENTS];
            for (int c = 0; c < j->components; c++) {
                s[c] = rows[c][x * j->comp[c].h / j->h_max];
            }
            int r, g, b;
            if (j->components == 1) {
                r = g = b = s[0]; // grey
            } else if (j->rgb) {
                r = s[0];
                g = s[1];
                b = s[2];
            } else {
                int cb = s[1] - 128, cr = s[2] - 128;
                r = clamp(s[0] + ((cr * 91881 + 32768) >> 16));
                g = clamp(s[0] + ((-22554 * cb - 46802 * cr + 32768) >> 16));
                b = clamp(s[0] + ((cb * 116130 + 32768) >> 16));
            }
            dest[x] = 0xFF000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
        }
    }
}

// ---- the whole file ---------------------------------------------------------------------------

// is m a "start of frame" marker (C0 to CF, but not C4 DHT, C8 or CC, which are other things)?
static bool is_sof(int m) {
    return m >= 0xC0 && m <= 0xCF && m != 0xC4 && m != 0xC8 && m != 0xCC;
}

// a JPEG's size, from its start of frame marker (see picture.h)
bool jpeg_size(const uint8_t* data, uint32_t length, int* width, int* height) {
    uint32_t pos = 2;
    while (pos + 4 <= length) {
        if (data[pos] != 0xFF) {
            pos++;
            continue;
        }
        int m = data[pos + 1];
        if (m == 0xFF) {
            pos++; // fill bytes before a marker
            continue;
        }
        pos += 2;
        // markers that are just the two bytes, with nothing after them
        if ((m >= 0xD0 && m <= 0xD7) || m == 0x01 || m == 0x00) {
            continue;
        }
        if (m == 0xD9 || m == 0xDA) {
            return false; // the end, or the coded data, before a frame said its size
        }
        uint32_t size = (uint32_t)be16(data + pos);
        if (is_sof(m)) {
            if (pos + 7 > length) {
                return false;
            }
            *height = be16(data + pos + 3);
            *width = be16(data + pos + 5);
            return picture_size_allowed(*width, *height);
        }
        pos += size;
    }
    return false;
}

// let everything decoding used go
static void finish(jpeg_t* j) {
    for (int c = 0; c < MAX_COMPONENTS; c++) {
        free(j->comp[c].coefficients);
        free(j->comp[c].plane);
    }
    free(j);
}

// decode a JPEG (see picture.h)
bool jpeg_decode(const uint8_t* data, uint32_t length, picture_t* out) {
    jpeg_t* j = calloc(1, sizeof(jpeg_t));
    if (j == NULL) {
        return false;
    }
    j->data = data;
    j->length = length;
    bool frame = false; // a start of frame has come (and room has been made for the blocks)
    int scans = 0;
    int adobe = -1;     // Adobe's APP14 colour transform: 0 RGB, 1 YCbCr, -1 not said

    // the markers, in turn, until the end of image
    uint32_t pos = 2;
    while (pos + 4 <= length) {
        if (data[pos] != 0xFF) {
            pos++; // anything between markers is skipped
            continue;
        }
        int m = data[pos + 1];
        if (m == 0xFF) {
            pos++;
            continue;
        }
        pos += 2;
        if (m == 0xD9) {
            break; // end of image
        }
        if ((m >= 0xD0 && m <= 0xD7) || m == 0x01 || m == 0x00) {
            continue;
        }
        int size = be16(data + pos);
        if (size < 2 || pos + (uint32_t)size > length) {
            break; // a damaged (or cut off) file: show what has been decoded
        }
        const uint8_t* body = data + pos + 2;
        int body_length = size - 2;
        bool ok = true;
        if (m == 0xDB) {
            ok = read_dqt(j, body, body_length);
        } else if (m == 0xC4) {
            ok = read_dht(j, body, body_length);
        } else if (m == 0xC0 || m == 0xC1 || m == 0xC2) {
            j->progressive = m == 0xC2;
            ok = !frame && read_sof(j, body, body_length);
            frame = true;
        } else if (is_sof(m)) {
            ok = false; // lossless, hierarchical or arithmetic coded: not decoded here
        } else if (m == 0xDD) {
            j->restart_interval = body_length >= 2 ? be16(body) : 0;
        } else if (m == 0xEE) {
            if (body_length >= 12 && memcmp(body, "Adobe", 5) == 0) {
                adobe = body[11];
            }
        } else if (m == 0xDA) {
            if (!frame || !read_sos(j, body, body_length)) {
                ok = false;
            } else {
                // the coded data follows the SOS segment, up to the next marker
                j->pos = pos + (uint32_t)size;
                bool read = read_scan(j);
                scans++;
                if (!read) {
                    break; // damaged: show what has been decoded
                }
                pos = j->pos;
                continue;
            }
        }
        if (!ok) {
            finish(j);
            return false;
        }
        pos += (uint32_t)size;
    }
    if (!frame || scans == 0) {
        finish(j);
        return false;
    }
    if (adobe == 0 && j->components == 3) {
        j->rgb = true;
    } else if (adobe == 1) {
        j->rgb = false;
    }

    out->pixels = make_planes(j) ? malloc((uint32_t)j->width * (uint32_t)j->height * 4) : NULL;
    if (out->pixels == NULL) {
        finish(j);
        return false;
    }
    out->width = j->width;
    out->height = j->height;
    make_pixels(j, out->pixels);
    finish(j);
    return true;
}
