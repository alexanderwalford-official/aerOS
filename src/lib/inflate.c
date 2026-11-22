/*
    inflate.c - undoing deflate compression (see inflate.h for what deflate is)

    The parts, in the order the data needs them:
    - reading bits: deflate packs its codes into bytes starting from each byte's lowest bit
    - Huffman tables: built from how many bits each symbol's code has, then used to read
      symbols a bit at a time
    - the three kinds of block: stored, fixed codes and dynamic codes
    - copying repeats: a length and a distance back into what has been written so far
*/

#include "inflate.h"
#include "aer_string.h"

// the longest code deflate uses, in bits
#define MAX_BITS 15
// symbols in each alphabet: 288 literal / length codes (0 to 255 are bytes, 256 ends the
// block, 257 and up are lengths), 30 distance codes, 19 code length codes
#define MAX_LITERALS 288
#define MAX_DISTANCES 30

// where inflating is up to: the input being read, and the output being written
typedef struct {
    const uint8_t* in;
    uint32_t in_length;
    uint32_t in_pos;     // the next byte to read
    uint32_t bit_buffer; // bits read from the input but not used yet, lowest first
    uint32_t bit_count;  // how many bits are in bit_buffer
    bool ran_out;        // tried to read past the end of the input (damaged data)
    uint8_t* out;
    uint32_t out_length;
    uint32_t out_pos;    // the next byte to write
} inflater_t;

// A Huffman table, the way puff.c keeps one: how many codes there are of each length, and the
// symbols in code order. That is all it takes, as deflate's codes are "canonical": codes of the
// same length are numbers one after another, given to the symbols in order
typedef struct {
    uint16_t count[MAX_BITS + 1]; // count[n]: how many symbols have an n bit code
    uint16_t symbol[MAX_LITERALS]; // the symbols, shortest codes first, then by symbol number
} huffman_t;

// the length codes 257 to 285: the smallest length each stands for, and how many extra bits
// follow to say how much more (285 is exactly 258)
static const uint16_t LENGTH_BASE[29] = {
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
    35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258
};
static const uint8_t LENGTH_EXTRA[29] = {
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
    3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0
};
// the same for the distance codes 0 to 29
static const uint16_t DISTANCE_BASE[30] = {
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
    257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577
};
static const uint8_t DISTANCE_EXTRA[30] = {
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
    7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13
};
// the order a dynamic block sends the code length code's lengths in (the ones most likely to
// be used first, so the rest can be left off)
static const uint8_t CODE_LENGTH_ORDER[19] = {
    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
};

// the next n bits of the input (n up to 16), the first one read being the lowest. Past the end
// of the input it gives 0s and sets ran_out
static uint32_t bits(inflater_t* s, uint32_t n) {
    // top the buffer up a byte at a time until it holds enough
    while (s->bit_count < n) {
        if (s->in_pos >= s->in_length) {
            s->ran_out = true;
            return 0;
        }
        s->bit_buffer |= (uint32_t)s->in[s->in_pos++] << s->bit_count;
        s->bit_count += 8;
    }
    uint32_t value = s->bit_buffer & ((1u << n) - 1);
    s->bit_buffer >>= n;
    s->bit_count -= n;
    return value;
}

// Build a Huffman table from each symbol's code length (0 for symbols that aren't used).
// False if the lengths make no sense (more codes of some length than there is room for)
static bool build(huffman_t* h, const uint8_t* lengths, int symbols) {
    int offsets[MAX_BITS + 1];
    memset(h->count, 0, sizeof(h->count));
    for (int s = 0; s < symbols; s++) {
        h->count[lengths[s]]++;
    }
    // check there is room: there are 2 one bit codes, each code not used can be split into two
    // codes a bit longer, and so on. Using more than there is room for can't be decoded
    int left = 1;
    for (int len = 1; len <= MAX_BITS; len++) {
        left <<= 1;
        left -= h->count[len];
        if (left < 0) {
            return false;
        }
    }
    // where each length's symbols start in symbol[], then put them there in symbol order
    offsets[1] = 0;
    for (int len = 1; len < MAX_BITS; len++) {
        offsets[len + 1] = offsets[len] + h->count[len];
    }
    for (int s = 0; s < symbols; s++) {
        if (lengths[s] != 0) {
            h->symbol[offsets[lengths[s]]++] = (uint16_t)s;
        }
    }
    return true;
}

// Read one symbol with a Huffman table, a bit at a time (puff.c's way). The codes of each length
// are consecutive numbers, so after each bit: if the code so far is one of this length's codes,
// that's the symbol. -1 for a code the table doesn't have (damaged data)
static int decode(inflater_t* s, const huffman_t* h) {
    int code = 0;  // the bits read so far
    int first = 0; // the first code of the current length
    int index = 0; // where the current length's symbols start in symbol[]
    for (int len = 1; len <= MAX_BITS; len++) {
        code |= (int)bits(s, 1);
        int count = h->count[len];
        if (code - first < count) {
            return h->symbol[index + (code - first)];
        }
        // not this length: move on to the codes one bit longer
        index += count;
        first += count;
        first <<= 1;
        code <<= 1;
    }
    return -1;
}

// a stored block: a length, the same length with every bit flipped (a check), and that many
// bytes as they are. It starts at the next whole byte
static bool stored(inflater_t* s) {
    // forget the bits left over from the byte the block header was in
    s->bit_buffer = 0;
    s->bit_count = 0;
    if (s->in_pos + 4 > s->in_length) {
        return false;
    }
    uint32_t length = s->in[s->in_pos] | ((uint32_t)s->in[s->in_pos + 1] << 8);
    uint32_t check = s->in[s->in_pos + 2] | ((uint32_t)s->in[s->in_pos + 3] << 8);
    s->in_pos += 4;
    if (length != (~check & 0xFFFF) || s->in_pos + length > s->in_length ||
        s->out_pos + length > s->out_length) {
        return false;
    }
    memcpy(s->out + s->out_pos, s->in + s->in_pos, length);
    s->in_pos += length;
    s->out_pos += length;
    return true;
}

// Read the symbols of a block with these two tables, writing bytes and copying repeats, until
// the end of block symbol (256). False if the data is damaged
static bool codes(inflater_t* s, const huffman_t* literals, const huffman_t* distances) {
    for (;;) {
        int symbol = decode(s, literals);
        if (symbol < 0 || s->ran_out) {
            return false;
        }
        if (symbol < 256) {
            // a byte, written as it is
            if (s->out_pos >= s->out_length) {
                return false;
            }
            s->out[s->out_pos++] = (uint8_t)symbol;
        } else if (symbol == 256) {
            return true; // the end of the block
        } else {
            // a repeat: how long it is, then how far back it starts
            symbol -= 257;
            if (symbol >= 29) {
                return false;
            }
            uint32_t length = LENGTH_BASE[symbol] + bits(s, LENGTH_EXTRA[symbol]);
            int d = decode(s, distances);
            if (d < 0 || d >= 30) {
                return false;
            }
            uint32_t distance = DISTANCE_BASE[d] + bits(s, DISTANCE_EXTRA[d]);
            if (distance > s->out_pos || s->out_pos + length > s->out_length) {
                return false;
            }
            // a byte at a time, as the repeat can run into the bytes it is writing: "back 1,
            // copy 5" after an "a" writes "aaaaa"
            for (uint32_t i = 0; i < length; i++) {
                s->out[s->out_pos] = s->out[s->out_pos - distance];
                s->out_pos++;
            }
        }
    }
}

// a block with the fixed codes: every deflate decoder knows these lengths, so nothing is sent
static bool fixed(inflater_t* s) {
    static huffman_t literals, distances;
    static bool built = false;
    if (!built) {
        uint8_t lengths[MAX_LITERALS];
        int i = 0;
        for (; i < 144; i++) lengths[i] = 8;
        for (; i < 256; i++) lengths[i] = 9;
        for (; i < 280; i++) lengths[i] = 7;
        for (; i < MAX_LITERALS; i++) lengths[i] = 8;
        build(&literals, lengths, MAX_LITERALS);
        for (i = 0; i < MAX_DISTANCES; i++) lengths[i] = 5;
        build(&distances, lengths, MAX_DISTANCES);
        built = true;
    }
    return codes(s, &literals, &distances);
}

// A block with codes of its own. Its code lengths come first, squeezed too: a little Huffman
// code (the "code length code") for the lengths 0 to 15, and for runs of them:
//     16 = the length before, 3 to 6 more times    17 = 3 to 10 zeros    18 = 11 to 138 zeros
static bool dynamic(inflater_t* s) {
    huffman_t literals, distances, lengths_code;
    uint8_t lengths[MAX_LITERALS + MAX_DISTANCES];
    uint32_t literal_count = bits(s, 5) + 257;
    uint32_t distance_count = bits(s, 5) + 1;
    uint32_t length_count = bits(s, 4) + 4;
    if (literal_count > MAX_LITERALS || distance_count > MAX_DISTANCES) {
        return false;
    }

    // the code length code's own lengths, 3 bits each, in CODE_LENGTH_ORDER
    memset(lengths, 0, 19);
    for (uint32_t i = 0; i < length_count; i++) {
        lengths[CODE_LENGTH_ORDER[i]] = (uint8_t)bits(s, 3);
    }
    if (!build(&lengths_code, lengths, 19)) {
        return false;
    }

    // then the literal and distance codes' lengths, one after the other
    uint32_t total = literal_count + distance_count;
    uint32_t i = 0;
    while (i < total) {
        int symbol = decode(s, &lengths_code);
        if (symbol < 0 || s->ran_out) {
            return false;
        }
        if (symbol < 16) {
            lengths[i++] = (uint8_t)symbol;
            continue;
        }
        uint8_t length = 0;
        uint32_t repeat;
        if (symbol == 16) {
            if (i == 0) {
                return false; // nothing before it to repeat
            }
            length = lengths[i - 1];
            repeat = 3 + bits(s, 2);
        } else if (symbol == 17) {
            repeat = 3 + bits(s, 3);
        } else {
            repeat = 11 + bits(s, 7);
        }
        if (i + repeat > total) {
            return false;
        }
        while (repeat--) {
            lengths[i++] = length;
        }
    }

    // a block with no end of block code could never finish
    if (lengths[256] == 0) {
        return false;
    }
    if (!build(&literals, lengths, (int)literal_count) ||
        !build(&distances, lengths + literal_count, (int)distance_count)) {
        return false;
    }
    return codes(s, &literals, &distances);
}

// undo zlib compression (see inflate.h)
bool inflate_zlib(const uint8_t* in, uint32_t in_length, uint8_t* out, uint32_t out_length, uint32_t* written) {
    *written = 0;
    // the zlib header: compression method 8 (deflate), a check that the two bytes as one
    // number divide by 31, and no preset dictionary (PNG never uses one)
    if (in_length < 2 || (in[0] & 0x0F) != 8 || ((in[0] << 8) | in[1]) % 31 != 0 || (in[1] & 0x20)) {
        return false;
    }
    inflater_t s;
    memset(&s, 0, sizeof(s));
    s.in = in;
    s.in_length = in_length;
    s.in_pos = 2;
    s.out = out;
    s.out_length = out_length;

    // each block starts with a bit saying whether it is the last, then two for its kind
    bool last = false;
    while (!last) {
        last = bits(&s, 1) == 1;
        uint32_t kind = bits(&s, 2);
        bool ok;
        if (kind == 0) {
            ok = stored(&s);
        } else if (kind == 1) {
            ok = fixed(&s);
        } else if (kind == 2) {
            ok = dynamic(&s);
        } else {
            ok = false; // 3 isn't a kind of block
        }
        if (!ok || s.ran_out) {
            return false;
        }
    }
    *written = s.out_pos;
    return true;
}
