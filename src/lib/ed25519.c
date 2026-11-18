/*
    ed25519.c - SHA-512, and checking Ed25519 signatures (see ed25519.h for what they are for)

    THE IDEA, WITHOUT THE MATHS
    Ed25519 works with points on a curve (an "Edwards curve", hence the Ed), where two points can
    be added to give a third, and so a point can be multiplied by a whole number n: added to
    itself n times. Going forwards is quick; going back (from n x B, finding n) is impossibly slow.
    That one-way street is what keeps the private key private.

        private key:  a secret number a
        public key:   A = a x B, where B is a fixed point everyone agrees on (the "base point")
        signing:      picks a secret r, gives R = r x B, and s = r + h x a, where h is a SHA-512
                      digest of R, A and the message (reduced to fit, see reduce)
        checking:     s x B  must equal  R + h x A

    Checking works because s x B = (r + h x a) x B = r x B + h x (a x B) = R + h x A, and only
    someone who knew a could have made s fit. Change one byte of the message and h changes, so
    nothing fits any more.

    THE PARTS, BOTTOM UP
        SHA-512                     the digest (with its constants worked out, not typed in)
        numbers mod p               "field elements": the coordinates of points, p = 2^255 - 19
        points                      adding, multiplying by a number, and the 32 byte form
        numbers mod L               h and s are kept below L, the number of points B can reach
        ed25519_verify              the check above

    The curve arithmetic follows TweetNaCl (tweetnacl.cr.yp.to, public domain), written out with
    longer names. Each step is checked, with the rest, by ed25519_selftest at every boot.
*/

#include "ed25519.h"
#include "crypto.h"
#include "aer_string.h"

// =================================================================================================
//  SHA-512 (FIPS 180-4)
// =================================================================================================
//
// Like SHA-256 (lib/crypto.c), but on 64 bit words, in 128 byte blocks, with 80 rounds. Its
// constants aren't typed in here: the standard defines them as the first 64 bits of the
// fractional parts of the square roots of the first 8 primes (the starting state) and of the
// cube roots of the first 80 primes (one for each round), and work_out_constants does exactly
// that, once. A mistyped constant is then impossible, and the self-test proves the result.

static uint64_t K[80];          // the round constants
static uint64_t H0[8];          // the starting state
static bool constants_ready = false;

// ---- whole roots of big numbers, for the constants ----

// a number of up to 256 bits, as eight 32 bit pieces, the lowest first
typedef struct {
    uint32_t limb[8];
} big_t;

// a x b (only the lowest 256 bits are kept: the numbers here never get that big)
static void big_multiply (const big_t* a, const big_t* b, big_t* out) {
    big_t result;
    memset(&result, 0, sizeof(result));
    for (int i = 0; i < 8; i++) {
        uint64_t carry = 0;
        for (int j = 0; i + j < 8; j++) {
            uint64_t sum = (uint64_t)a->limb[i] * b->limb[j] + result.limb[i + j] + carry;
            result.limb[i + j] = (uint32_t)sum;
            carry = sum >> 32;
        }
    }
    *out = result;
}

// is a bigger than b?
static bool big_above (const big_t* a, const big_t* b) {
    for (int i = 7; i >= 0; i--) {
        if (a->limb[i] != b->limb[i]) {
            return a->limb[i] > b->limb[i];
        }
    }
    return false;
}

// The whole square root (degree 2) or cube root (degree 3) of n, found one bit at a time from
// the top: a bit stays if the root with it, squared or cubed, is still no bigger than n. The
// roots here are under 2^70, so 70 bits are tried
static uint64_t big_root_low_64 (const big_t* n, int degree) {
    big_t root;
    memset(&root, 0, sizeof(root));
    for (int bit = 69; bit >= 0; bit--) {
        big_t trial = root;
        trial.limb[bit / 32] |= 1u << (bit % 32);
        big_t power = trial;
        for (int d = 1; d < degree; d++) {
            big_multiply(&power, &trial, &power);
        }
        if (!big_above(&power, n)) {
            root = trial;
        }
    }
    // the bottom 64 bits: the fractional part, as the whole part sits above them (see below)
    return ((uint64_t)root.limb[1] << 32) | root.limb[0];
}

// Work out K and H0. The fractional part of the cube root of p, as 64 bits, is the bottom 64
// bits of the whole cube root of p x 2^192 (that is, the cube root of p multiplied by 2^64), and
// likewise the square root of p x 2^128 for H0
static void work_out_constants (void) {
    int found = 0;
    for (uint32_t candidate = 2; found < 80; candidate++) {
        // a prime: nothing from 2 up to its square root divides it
        bool prime = true;
        for (uint32_t d = 2; d * d <= candidate; d++) {
            if (candidate % d == 0) {
                prime = false;
                break;
            }
        }
        if (!prime) {
            continue;
        }
        big_t n;
        memset(&n, 0, sizeof(n));
        n.limb[6] = candidate;                    // p x 2^192 (6 limbs of 32 bits up)
        K[found] = big_root_low_64(&n, 3);
        if (found < 8) {
            memset(&n, 0, sizeof(n));
            n.limb[4] = candidate;                // p x 2^128
            H0[found] = big_root_low_64(&n, 2);
        }
        found++;
    }
    constants_ready = true;
}

// ---- the hash itself ----

typedef struct {
    uint64_t state[8];
    uint64_t length;      // bytes hashed so far
    uint8_t block[128];   // the block being filled
    size_t used;          // bytes in it
} sha512_t;

// turn a 64 bit word round by n bits (what falls off the right comes back on the left)
static uint64_t rotate (uint64_t x, int n) {
    return (x >> n) | (x << (64 - n));
}

// mix one 128 byte block into the state: FIPS 180-4 section 6.4.2
static void sha512_block (sha512_t* s, const uint8_t* block) {
    uint64_t w[80];
    for (int t = 0; t < 16; t++) {
        // the block's bytes, 8 at a time, highest first
        w[t] = 0;
        for (int i = 0; i < 8; i++) {
            w[t] = (w[t] << 8) | block[t * 8 + i];
        }
    }
    for (int t = 16; t < 80; t++) {
        uint64_t s0 = rotate(w[t - 15], 1) ^ rotate(w[t - 15], 8) ^ (w[t - 15] >> 7);
        uint64_t s1 = rotate(w[t - 2], 19) ^ rotate(w[t - 2], 61) ^ (w[t - 2] >> 6);
        w[t] = w[t - 16] + s0 + w[t - 7] + s1;
    }
    uint64_t a = s->state[0], b = s->state[1], c = s->state[2], d = s->state[3];
    uint64_t e = s->state[4], f = s->state[5], g = s->state[6], h = s->state[7];
    for (int t = 0; t < 80; t++) {
        uint64_t sum1 = rotate(e, 14) ^ rotate(e, 18) ^ rotate(e, 41);
        uint64_t choose = (e & f) ^ (~e & g);
        uint64_t first = h + sum1 + choose + K[t] + w[t];
        uint64_t sum0 = rotate(a, 28) ^ rotate(a, 34) ^ rotate(a, 39);
        uint64_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint64_t second = sum0 + majority;
        h = g;
        g = f;
        f = e;
        e = d + first;
        d = c;
        c = b;
        b = a;
        a = first + second;
    }
    s->state[0] += a;
    s->state[1] += b;
    s->state[2] += c;
    s->state[3] += d;
    s->state[4] += e;
    s->state[5] += f;
    s->state[6] += g;
    s->state[7] += h;
    wipe(w, sizeof(w));
}

// start a hash
static void sha512_init (sha512_t* s) {
    if (!constants_ready) {
        work_out_constants();
    }
    memcpy(s->state, H0, sizeof(H0));
    s->length = 0;
    s->used = 0;
}

// feed it bytes
static void sha512_update (sha512_t* s, const void* data, size_t length) {
    const uint8_t* bytes = data;
    s->length += length;
    while (length > 0) {
        size_t take = sizeof(s->block) - s->used;
        if (take > length) {
            take = length;
        }
        memcpy(s->block + s->used, bytes, take);
        s->used += take;
        bytes += take;
        length -= take;
        if (s->used == sizeof(s->block)) {
            sha512_block(s, s->block);
            s->used = 0;
        }
    }
}

// Finish: a 1 bit (0x80), zeros, then the length in bits in the last 16 bytes (the top 8 are 0
// here: nothing hashed is anywhere near 2^61 bytes). Then the state, highest byte first
static void sha512_final (sha512_t* s, uint8_t out[SHA512_SIZE]) {
    uint64_t bits = s->length * 8;
    uint8_t one = 0x80;
    uint8_t zero = 0;
    sha512_update(s, &one, 1);
    while (s->used != 112) {
        sha512_update(s, &zero, 1);
    }
    uint8_t length[16];
    memset(length, 0, sizeof(length));
    for (int i = 0; i < 8; i++) {
        length[15 - i] = (uint8_t)(bits >> (8 * i));
    }
    sha512_update(s, length, sizeof(length));
    for (int i = 0; i < 8; i++) {
        for (int j = 0; j < 8; j++) {
            out[i * 8 + j] = (uint8_t)(s->state[i] >> (56 - 8 * j));
        }
    }
    wipe(s, sizeof(sha512_t));
}

// the digest of some bytes, in one go (see ed25519.h)
void sha512 (const void* data, size_t length, uint8_t out[SHA512_SIZE]) {
    sha512_t s;
    sha512_init(&s);
    sha512_update(&s, data, length);
    sha512_final(&s, out);
}


// =================================================================================================
//  Numbers mod p = 2^255 - 19 (the coordinates of points)
// =================================================================================================
//
// A number below p needs 255 bits. It is kept as 16 pieces ("limbs") of 16 bits, the lowest
// first, each in a 64 bit signed number: that leaves room to multiply two pieces, and to add up
// many of those products, before carrying. carry() then moves what overflows each piece into the
// next. Overflow from the top piece is worth 2^256 = 38 mod p (as 2^255 = 19), so it comes back
// round to the bottom multiplied by 38.

typedef int64_t fe[16];

// carry the overflow of each piece into the next, so each is back to 16 bits (or a little more)
static void fe_carry (fe o) {
    for (int i = 0; i < 16; i++) {
        o[i] += 1 << 16;
        int64_t c = o[i] >> 16;
        if (i < 15) {
            o[i + 1] += c - 1;
        } else {
            o[0] += 38 * (c - 1);     // past the top: 2^256 is 38 mod p
        }
        o[i] -= c * 65536;
    }
}

// swap p and q if b is 1, leave them if it is 0, taking the same time either way (no "if" on a
// secret, so timing gives nothing away)
static void fe_swap (fe p, fe q, int b) {
    int64_t mask = ~(int64_t)(b - 1);   // all ones for b = 1, all zeros for b = 0
    for (int i = 0; i < 16; i++) {
        int64_t t = mask & (p[i] ^ q[i]);
        p[i] ^= t;
        q[i] ^= t;
    }
}

// the 32 byte form of n, fully reduced (below p), lowest byte first
static void fe_pack (uint8_t out[32], const fe n) {
    fe t;
    fe m;
    memcpy(t, n, sizeof(fe));
    fe_carry(t);
    fe_carry(t);
    fe_carry(t);
    // take p away twice where it fits, to land below p
    for (int round = 0; round < 2; round++) {
        m[0] = t[0] - 0xffed;
        for (int i = 1; i < 15; i++) {
            m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
            m[i - 1] &= 0xffff;
        }
        m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
        int borrowed = (int)((m[15] >> 16) & 1);
        m[14] &= 0xffff;
        fe_swap(t, m, 1 - borrowed);
    }
    for (int i = 0; i < 16; i++) {
        out[2 * i] = (uint8_t)(t[i] & 0xff);
        out[2 * i + 1] = (uint8_t)(t[i] >> 8);
    }
}

// do a and b stand for the same number mod p?
static bool fe_equal (const fe a, const fe b) {
    uint8_t x[32];
    uint8_t y[32];
    fe_pack(x, a);
    fe_pack(y, b);
    return crypto_equal(x, y, 32);
}

// is n odd (once fully reduced)? The 32 byte form of a point keeps x's oddness in its top bit
static int fe_odd (const fe n) {
    uint8_t x[32];
    fe_pack(x, n);
    return x[0] & 1;
}

// read a number from its 32 byte form (the top bit, which isn't part of it, is left out)
static void fe_unpack (fe o, const uint8_t in[32]) {
    for (int i = 0; i < 16; i++) {
        o[i] = in[2 * i] + ((int64_t)in[2 * i + 1] << 8);
    }
    o[15] &= 0x7fff;
}

// o = a + b (no carrying needed: the pieces have room)
static void fe_add (fe o, const fe a, const fe b) {
    for (int i = 0; i < 16; i++) {
        o[i] = a[i] + b[i];
    }
}

// o = a - b
static void fe_subtract (fe o, const fe a, const fe b) {
    for (int i = 0; i < 16; i++) {
        o[i] = a[i] - b[i];
    }
}

// o = a x b, multiplying piece by piece like long multiplication, then folding the top half back
// in (each piece past 16 is worth 38 times the piece 16 places lower, as for carrying above)
static void fe_multiply (fe o, const fe a, const fe b) {
    int64_t t[31];
    memset(t, 0, sizeof(t));
    for (int i = 0; i < 16; i++) {
        for (int j = 0; j < 16; j++) {
            t[i + j] += a[i] * b[j];
        }
    }
    for (int i = 0; i < 15; i++) {
        t[i] += 38 * t[i + 16];
    }
    for (int i = 0; i < 16; i++) {
        o[i] = t[i];
    }
    fe_carry(o);
    fe_carry(o);
}

// o = a x a
static void fe_square (fe o, const fe a) {
    fe_multiply(o, a, a);
}

// o = 1 / n mod p: n to the power p - 2 (Fermat's little theorem), by squaring and multiplying.
// Going down the bits of p - 2 = 2^255 - 21: square every time, multiply for each 1 bit
static void fe_invert (fe o, const fe n) {
    fe c;
    memcpy(c, n, sizeof(fe));
    for (int bit = 253; bit >= 0; bit--) {
        fe_square(c, c);
        if (bit != 2 && bit != 4) {
            fe_multiply(c, c, n);
        }
    }
    memcpy(o, c, sizeof(fe));
}

// o = n to the power 2^252 - 3, the step in finding a square root mod p (see decode_negated)
static void fe_pow_2_252_minus_3 (fe o, const fe n) {
    fe c;
    memcpy(c, n, sizeof(fe));
    for (int bit = 250; bit >= 0; bit--) {
        fe_square(c, c);
        if (bit != 1) {
            fe_multiply(c, c, n);
        }
    }
    memcpy(o, c, sizeof(fe));
}

// a small whole number as a field element
static void fe_small (fe o, int64_t value) {
    memset(o, 0, sizeof(fe));
    o[0] = value & 0xffff;
    o[1] = value >> 16;
}


// =================================================================================================
//  Points on the curve
// =================================================================================================
//
// The curve is -x^2 + y^2 = 1 + d x^2 y^2 (mod p), with d = -121665 / 121666. A point is kept as
// four numbers X, Y, Z, T standing for x = X/Z, y = Y/Z and x y = T/Z ("extended coordinates"),
// which lets points be added without a slow division each time. The point (0, 1) is the "zero":
// adding it changes nothing.

typedef struct {
    fe x, y, z, t;
} point_t;

static fe D;            // d, the curve's number
static fe D2;           // 2d
static fe ROOT_MINUS_1; // a square root of -1 mod p
static point_t BASE;    // the base point B
static bool curve_ready = false;

// p = p + q: the curve's addition formula, for extended coordinates
static void point_add (point_t* p, const point_t* q) {
    fe a, b, c, d, t, e, f, g, h;
    fe_subtract(a, p->y, p->x);
    fe_subtract(t, q->y, q->x);
    fe_multiply(a, a, t);
    fe_add(b, p->x, p->y);
    fe_add(t, q->x, q->y);
    fe_multiply(b, b, t);
    fe_multiply(c, p->t, q->t);
    fe_multiply(c, c, D2);
    fe_multiply(d, p->z, q->z);
    fe_add(d, d, d);
    fe_subtract(e, b, a);
    fe_subtract(f, d, c);
    fe_add(g, d, c);
    fe_add(h, b, a);
    fe_multiply(p->x, e, f);
    fe_multiply(p->y, h, g);
    fe_multiply(p->z, g, f);
    fe_multiply(p->t, e, h);
}

// swap two points if b is 1, in the same time either way (see fe_swap)
static void point_swap (point_t* p, point_t* q, int b) {
    fe_swap(p->x, q->x, b);
    fe_swap(p->y, q->y, b);
    fe_swap(p->z, q->z, b);
    fe_swap(p->t, q->t, b);
}

// The 32 byte form of a point: y, with the oddness of x in the top bit (x can be worked out
// from y, up to its sign: see decode_negated)
static void point_encode (uint8_t out[32], const point_t* p) {
    fe z_inverse, x, y;
    fe_invert(z_inverse, p->z);
    fe_multiply(x, p->x, z_inverse);
    fe_multiply(y, p->y, z_inverse);
    fe_pack(out, y);
    out[31] ^= (uint8_t)(fe_odd(x) << 7);
}

// p = n x q (n is 32 bytes, lowest first), by "double and add" down n's bits. Both points are
// worked on every time, swapped by the bit rather than chosen by it, so the time it takes
// doesn't depend on n. q is changed on the way
static void point_multiply (point_t* p, point_t* q, const uint8_t n[32]) {
    fe_small(p->x, 0);
    fe_small(p->y, 1);
    fe_small(p->z, 1);
    fe_small(p->t, 0);
    for (int bit = 255; bit >= 0; bit--) {
        int b = (n[bit / 8] >> (bit & 7)) & 1;
        point_swap(p, q, b);
        point_add(q, p);
        point_add(p, p);
        point_swap(p, q, b);
    }
}

// Read a point from its 32 byte form, and give back its negative (x turned round), which is what
// checking needs. y is read directly; x comes from the curve's equation, x^2 = (y^2 - 1) /
// (d y^2 + 1), by a square root mod p (worked out as in RFC 8032 section 5.1.3). False if there
// is no such point
static bool decode_negated (point_t* p, const uint8_t in[32]) {
    fe numerator, denominator, d2, d4, d6, t, check;
    fe one;
    fe_small(one, 1);
    fe_small(p->z, 1);
    fe_unpack(p->y, in);
    fe_square(numerator, p->y);                // y^2
    fe_multiply(denominator, numerator, D);    // d y^2
    fe_subtract(numerator, numerator, one);    // y^2 - 1
    fe_add(denominator, one, denominator);     // d y^2 + 1

    // a candidate root: (u / v)^((p + 3) / 8), worked out without dividing
    fe_square(d2, denominator);
    fe_square(d4, d2);
    fe_multiply(d6, d4, d2);
    fe_multiply(t, d6, numerator);
    fe_multiply(t, t, denominator);
    fe_pow_2_252_minus_3(t, t);
    fe_multiply(t, t, numerator);
    fe_multiply(t, t, denominator);
    fe_multiply(t, t, denominator);
    fe_multiply(p->x, t, denominator);

    // its square is either right, or right times -1 (then multiplying by sqrt(-1) mends it)...
    fe_square(check, p->x);
    fe_multiply(check, check, denominator);
    if (!fe_equal(check, numerator)) {
        fe_multiply(p->x, p->x, ROOT_MINUS_1);
    }
    // ...or there is no square root at all: not a point on the curve
    fe_square(check, p->x);
    fe_multiply(check, check, denominator);
    if (!fe_equal(check, numerator)) {
        return false;
    }
    // pick the x whose oddness is the opposite of the top bit's, which is the point's negative
    fe zero;
    fe_small(zero, 0);
    if (fe_odd(p->x) == (in[31] >> 7)) {
        fe_subtract(p->x, zero, p->x);
    }
    fe_multiply(p->t, p->x, p->y);
    return true;
}

// Work out d, 2d, sqrt(-1) and the base point B once, rather than type their long numbers in:
//     d = -121665 / 121666
//     sqrt(-1) = 2^((p - 1) / 4), as 2 has no square root mod p (so 2^((p - 1) / 2) = -1)
//     B is the point with y = 4/5 and an even x: its 32 byte form is 58 66 66 ... 66
static void setup_curve (void) {
    fe a, b, zero;
    fe_small(zero, 0);
    fe_small(a, 121665);
    fe_small(b, 121666);
    fe_invert(b, b);
    fe_multiply(D, a, b);
    fe_subtract(D, zero, D);
    fe_add(D2, D, D);

    fe two;
    fe_small(two, 2);
    fe_pow_2_252_minus_3(a, two);              // 2^(2^252 - 3)
    fe_square(a, a);                           // 2^(2^253 - 6)
    fe_multiply(ROOT_MINUS_1, a, two);         // 2^(2^253 - 5) = 2^((p - 1) / 4)

    uint8_t base[32];
    memset(base, 0x66, sizeof(base));
    base[0] = 0x58;
    decode_negated(&BASE, base);               // that gives -B...
    fe_subtract(BASE.x, zero, BASE.x);         // ...so turn x round again
    fe_multiply(BASE.t, BASE.x, BASE.y);
    curve_ready = true;
}


// =================================================================================================
//  Numbers mod L, the number of points the base point reaches
// =================================================================================================
//
// L = 2^252 + 27742317777372353535851937790883648493. h, a 64 byte digest, is reduced mod L
// before use, and s must already be below L (or the same signature could be written two ways).

static const uint8_t L[32] = {
    0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58, 0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10,
};

// r = x mod L, for a 64 byte number x (kept in 64 bit pieces of one byte each, so they can go
// negative on the way), taking away multiples of L from the top byte down
static void mod_l (uint8_t r[32], int64_t x[64]) {
    int64_t carry;
    for (int i = 63; i >= 32; i--) {
        carry = 0;
        int j;
        for (j = i - 32; j < i - 12; j++) {
            x[j] += carry - 16 * x[i] * L[j - (i - 32)];
            carry = (x[j] + 128) >> 8;
            x[j] -= carry * 256;
        }
        x[j] += carry;
        x[i] = 0;
    }
    carry = 0;
    for (int j = 0; j < 32; j++) {
        x[j] += carry - (x[31] >> 4) * L[j];
        carry = x[j] >> 8;
        x[j] &= 255;
    }
    for (int j = 0; j < 32; j++) {
        x[j] -= carry * L[j];
    }
    for (int i = 0; i < 32; i++) {
        x[i + 1] += x[i] >> 8;
        r[i] = (uint8_t)(x[i] & 255);
    }
}

// a 64 byte digest, reduced mod L in place (the 32 bytes after it become 0)
static void reduce (uint8_t h[64]) {
    int64_t x[64];
    for (int i = 0; i < 64; i++) {
        x[i] = h[i];
    }
    memset(h, 0, 64);
    mod_l(h, x);
}

// is the 32 byte number s (lowest byte first) below L?
static bool below_l (const uint8_t s[32]) {
    for (int i = 31; i >= 0; i--) {
        if (s[i] != L[i]) {
            return s[i] < L[i];
        }
    }
    return false; // equal to L
}


// =================================================================================================
//  Checking a signature
// =================================================================================================

// is this a signature of the message by the public key's owner? (see ed25519.h and the top)
bool ed25519_verify (const uint8_t signature[ED25519_SIGNATURE_SIZE], const void* message, size_t length, const uint8_t public_key[ED25519_KEY_SIZE]) {
    if (!curve_ready) {
        setup_curve();
    }
    // the signature is R (32 bytes, a point) then s (32 bytes, a number below L)
    const uint8_t* r = signature;
    const uint8_t* s = signature + 32;
    if (!below_l(s)) {
        return false;
    }
    point_t minus_a;
    if (!decode_negated(&minus_a, public_key)) {
        return false; // not a point on the curve: not a real public key
    }

    // h = SHA-512(R, A, message), reduced mod L
    uint8_t h[64];
    sha512_t digest;
    sha512_init(&digest);
    sha512_update(&digest, r, 32);
    sha512_update(&digest, public_key, ED25519_KEY_SIZE);
    sha512_update(&digest, message, length);
    sha512_final(&digest, h);
    reduce(h);

    // s x B - h x A must be R: then s x B = R + h x A, which only the private key could arrange
    point_t sum, s_times_b, base = BASE;
    point_multiply(&sum, &minus_a, h);          // -h x A
    point_multiply(&s_times_b, &base, s);       // s x B
    point_add(&sum, &s_times_b);
    uint8_t check[32];
    point_encode(check, &sum);
    return crypto_equal(check, r, 32);
}


// =================================================================================================
//  The self-test
// =================================================================================================

// does a result match the hex the standard gives?
static bool matches (const uint8_t* bytes, size_t length, const char* hex) {
    uint8_t expected[64];
    return crypto_from_hex(hex, expected, length) && crypto_equal(bytes, expected, length);
}

// one of RFC 8032's test signatures: true if it checks out, and false once any byte is changed
static bool check_test_signature (const char* key_hex, const uint8_t* message, size_t length, const char* signature_hex) {
    uint8_t key[32];
    uint8_t signature[64];
    if (!crypto_from_hex(key_hex, key, 32) || !crypto_from_hex(signature_hex, signature, 64)) {
        return false;
    }
    bool good = ed25519_verify(signature, message, length, key);
    signature[10] ^= 1; // and a signature with one bit changed must fail
    bool bad = ed25519_verify(signature, message, length, key);
    return good && !bad;
}

// SHA-512 and Ed25519 against the published test vectors (see ed25519.h)
bool ed25519_selftest (void) {
    bool ok = true;
    uint8_t digest[SHA512_SIZE];

    // FIPS 180-4's examples: one block, and two
    sha512("abc", 3, digest);
    ok &= matches(digest, 64, "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
                              "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");
    const char* two = "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopq"
                      "klmnopqrlmnopqrsmnopqrstnopqrstu";
    sha512(two, slen(two), digest);
    ok &= matches(digest, 64, "8e959b75dae313da8cf4f72814fc143f8f7779c6eb9f7fa17299aeadb6889018"
                              "501d289e4900f7e4331b99dec4b5433ac7d329eeb6dd26545e96e55b874be909");

    // RFC 8032 section 7.1, tests 1 to 3: an empty message, one byte, and two
    const uint8_t one[1] = { 0x72 };
    const uint8_t pair[2] = { 0xaf, 0x82 };
    ok &= check_test_signature("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", NULL, 0,
                               "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
                               "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b");
    ok &= check_test_signature("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", one, 1,
                               "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da0"
                               "85ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00");
    ok &= check_test_signature("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", pair, 2,
                               "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac1"
                               "8ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a");
    return ok;
}
