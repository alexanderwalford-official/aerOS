/*
    gfx.c - graphics for apps

    Draws into the framebuffer GRUB set up (see VGA_linear.c). A pixel is 2, 3 or 4 bytes, with
    red, green and blue at the bit positions GRUB reported.

    DRAWING STRAIGHT TO THE SCREEN, OR TO A BACK BUFFER

    By default everything is drawn straight onto the screen, so you see each step as it
    happens. That is fine for a still picture, but anything that moves flickers: the screen
    shows the frame half cleared and half redrawn.

    gfx_buffered(true) gives a "back buffer" instead: a copy of the screen in memory, in exactly
    the screen's own pixel format. Everything is drawn there, out of sight, and gfx_show()
    copies the finished frame onto the screen in one go, a row at a time with memcpy. The
    screen only ever shows whole frames. Every drawing function writes through `target`, which
    is either the screen or the back buffer:

        drawing --> target --(straight)----------------------------> screen
                          \--(buffered)--> back buffer --gfx_show()--> screen

    BMP pictures: a 14 byte file header ("BM", where the pixels start), a 40+ byte info header
    (width, height, bits per pixel, compression), an optional palette, then the rows of pixels.
    Rows are stored bottom up unless the height is negative, each one padded to a multiple of
    4 bytes, with pixels in blue, green, red order.
    https://en.wikipedia.org/wiki/BMP_file_format

    PNG, GIF and JPEG pictures are squeezed, so picture.c decodes them into pixels first (see
    picture.h), and gfx_image draws those the same way. Their see-through parts are mixed with
    what's already on the screen.
*/

#include "gfx.h"
#include "picture.h"
#include "VGA_linear.h"
#include "font8x16.h"
#include "../../lib/aer_stdlib.h"
#include "../../lib/aer_math.h"
#include "../../lib/aer_string.h"

static vga_framebuffer_t fb; // where the framebuffer is and how its pixels are laid out
static uint32_t fb_bytes;    // bytes per pixel
static bool active = false;  // between gfx_begin and gfx_end, every drawing call checks it

// where drawing goes: the screen itself, or the back buffer (see the top of this file)
static uint8_t* target;        // the first pixel
static uint32_t target_pitch;  // bytes from one row of pixels to the next
static uint8_t* back = NULL;   // the back buffer, NULL when drawing straight to the screen

// the spotlight textures are lit by (see gfx_texture_light): off (spot_r2 is 0), or a circle
// round spot_x, spot_y, spot_r2 being its radius squared. A pixel's distance from its middle,
// squared, times spot_scale and >> 16, goes from 0 in the middle up to 255 at the edge, and
// spot_table[] has how much light (0 to 256) there is at each. spot_outside is the light
// outside the circle (0, pitch black, unless an ambient light was asked for), and no entry of
// spot_table[] is darker than it. The table is only worked out again when the radius or the
// ambient light changes (table_radius, table_ambient)
static uint32_t spot_r2 = 0;
static uint32_t spot_scale;
static uint16_t spot_table[256];
static uint32_t spot_outside = 0;
static int spot_x, spot_y;
static int table_radius = 0;
static uint32_t table_ambient = 0;

// take the screen over from the text console (false in VGA text mode, there are no pixels)
bool gfx_begin(void) {
    if (!vga_framebuffer_info(&fb)) {
        return false;
    }
    fb_bytes = (fb.bpp + 7) / 8; // bits rounded up to whole bytes (15 bit colour takes 2)
    // drawing starts out going straight to the screen
    target = (uint8_t*)(uintptr_t)fb.address;
    target_pitch = fb.pitch;
    clear(BLACK); // hides the cursor, and tells the terminal to redraw itself afterwards
    // the console's cells[] no longer match the pixels once we draw over them
    vga_invalidate();
    active = true;
    spot_r2 = 0; // a spotlight a program left on doesn't carry over to the next one
    gfx_clear(0x000000);
    return true;
}

// give the screen back: the console redraws all of its text the next time it is used
void gfx_end(void) {
    if (!active) {
        return;
    }
    gfx_buffered(false); // shows the last frame and frees the back buffer, if there is one
    active = false;
    // every cell counts as changed, so the clear below repaints the whole screen over our pixels
    vga_invalidate();
    clear(BLACK);
}

// switch between drawing straight to the screen and drawing to a back buffer (see the top of
// this file), false if there are no graphics or no memory for the buffer
bool gfx_buffered(bool on) {
    if (!active) {
        return false;
    }
    uint8_t* screen = (uint8_t*)(uintptr_t)fb.address;
    // the back buffer's rows are exactly as long as the pixels in them, with none of the
    // padding the screen's rows may have (fb.pitch)
    uint32_t row_bytes = fb.width * fb_bytes;

    // switching on (asking twice does nothing)
    if (on && back == NULL) {
        back = malloc((size_t)row_bytes * fb.height);
        if (back == NULL) {
            return false; // not enough memory: carry on drawing straight to the screen
        }
        // start from what is on the screen now, so nothing drawn so far is lost
        for (uint32_t y = 0; y < fb.height; y++) {
            memcpy(back + y * row_bytes, screen + y * fb.pitch, row_bytes);
        }
        target = back;
        target_pitch = row_bytes;
    } else if (!on && back != NULL) {
        // switching off: drawing goes straight to the screen again
        gfx_show(); // the screen gets the last frame drawn
        free(back);
        back = NULL;
        target = screen;
        target_pitch = fb.pitch;
    }
    return true;
}

// copy the finished frame from the back buffer onto the screen
void gfx_show(void) {
    if (!active || back == NULL) {
        return; // drawing straight to the screen: it is already there
    }
    uint8_t* screen = (uint8_t*)(uintptr_t)fb.address;
    uint32_t row_bytes = fb.width * fb_bytes;
    // a row at a time, because the screen's rows may be longer than the picture (fb.pitch is
    // sometimes rounded up), while the back buffer's rows are exactly row_bytes long
    for (uint32_t y = 0; y < fb.height; y++) {
        memcpy(screen + y * fb.pitch, back + y * row_bytes, row_bytes);
    }
}

// the screen's width in pixels, 0 when graphics aren't on
uint32_t gfx_width(void) {
    return active ? fb.width : 0;
}

// the screen's height in pixels, 0 when graphics aren't on
uint32_t gfx_height(void) {
    return active ? fb.height : 0;
}

// turn 0xRRGGBB into the framebuffer's own pixel format. Each colour is cut down to however
// many bits the screen has for it and moved to where the screen keeps it, e.g.
//     32 bit (8:8:8)   red at bit 16, green at 8, blue at 0      -> 0x00RRGGBB
//     16 bit (5:6:5)   red at bit 11, green at 5, blue at 0      -> rrrrrggggggbbbbb
static uint32_t pack(uint32_t rgb) {
    // split 0xRRGGBB into its three bytes
    uint32_t r = (rgb >> 16) & 0xFF, g = (rgb >> 8) & 0xFF, b = rgb & 0xFF;
    // shifting right by 8 - size keeps the top `size` bits, the shift left puts them in place
    return ((r >> (8 - fb.red_size)) << fb.red_pos) |
           ((g >> (8 - fb.green_size)) << fb.green_pos) |
           ((b >> (8 - fb.blue_size)) << fb.blue_pos);
}

// write one packed pixel (the caller has checked x and y are on screen)
static void put(uint32_t x, uint32_t y, uint32_t pixel) {
    // the pixel's address: y whole rows down (pitch bytes each), then x pixels across
    uint8_t* p = target + y * target_pitch + x * fb_bytes;
    // there is no 24 bit type, so a 3 byte pixel goes a byte at a time, lowest byte first
    switch (fb_bytes) {
        case 4:
            *(uint32_t*)p = pixel;
            break;
        case 3:
            p[0] = pixel & 0xFF;
            p[1] = (pixel >> 8) & 0xFF;
            p[2] = (pixel >> 16) & 0xFF;
            break;
        default:
            *(uint16_t*)p = (uint16_t)pixel;
            break;
    }
}

// fill `count` pixels of one row, starting at x, y, with a packed pixel. 32 bit screens (the
// usual kind) are filled 4 bytes a step with "rep stosl" (see memset in lib/aer_string.c),
// which makes clearing the whole screen every frame cheap
static void fill_row(uint32_t x, uint32_t y, uint32_t count, uint32_t pixel) {
    if (fb_bytes == 4) {
        void* p = target + y * target_pitch + x * 4;
        // rep stosl stores EAX at [EDI] ECX times, moving EDI on 4 bytes each time. "+D" and
        // "+c" tell the compiler both registers change, "memory" that the pixels do too
        __asm__ volatile ("rep stosl" : "+D"(p), "+c"(count) : "a"(pixel) : "memory");
        return;
    }
    for (uint32_t i = 0; i < count; i++) {
        put(x + i, y, pixel);
    }
}

// one pixel at x, y in the colour 0xRRGGBB
// every public drawing call clips to the screen, so apps can draw partly (or wholly) off it
void gfx_pixel(int x, int y, uint32_t rgb) {
    if (active && x >= 0 && y >= 0 && (uint32_t)x < fb.width && (uint32_t)y < fb.height) {
        put((uint32_t)x, (uint32_t)y, pack(rgb));
    }
}

// a filled rectangle, w x h pixels with its top left corner at x, y
void gfx_rect(int x, int y, int w, int h, uint32_t rgb) {
    if (!active) {
        return;
    }
    // Clip to the screen. A rectangle starting off the left (or top) edge loses the part that is
    // off screen: e.g. x = -10, w = 50 becomes x = 0, w = 40
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (y < 0) {
        h += y;
        y = 0;
    }
    // nothing left of it, or it starts past the right or bottom edge
    if (w <= 0 || h <= 0 || (uint32_t)x >= fb.width || (uint32_t)y >= fb.height) {
        return;
    }
    // and cut off whatever hangs over the right and bottom edges
    if ((uint32_t)(x + w) > fb.width) {
        w = (int)fb.width - x;
    }
    if ((uint32_t)(y + h) > fb.height) {
        h = (int)fb.height - y;
    }

    uint32_t pixel = pack(rgb); // once, not for every pixel
    for (int row = 0; row < h; row++) {
        fill_row((uint32_t)x, (uint32_t)(y + row), (uint32_t)w, pixel);
    }
}

// fill the whole screen with one colour
void gfx_clear(uint32_t rgb) {
    gfx_rect(0, 0, (int)fb.width, (int)fb.height, rgb);
}

// a number without its sign: 5 for both 5 and -5
static int distance (int a, int b) {
    if (a > b) {
        return a - b;
    }
    return b - a;
}

// which way to step to get from a to b: +1, or -1
static int direction (int from, int to) {
    if (from < to) {
        return 1;
    }
    return -1;
}

// Draw a line with Bresenham's method, using only whole numbers.
// It moves one pixel at a time from (x0, y0) towards (x1, y1). `error` keeps track of how far
// the pixels drawn so far have drifted from the true line; whenever that drift gets to half a
// pixel in a direction, the next step goes that way too. A shallow line steps sideways every
// time and only sometimes up or down; a steep line the other way round
void gfx_line(int x0, int y0, int x1, int y1, uint32_t rgb) {
    int width = distance(x0, x1);
    int height = -distance(y0, y1); // negative: the maths below is neatest this way round
    int step_x = direction(x0, x1);
    int step_y = direction(y0, y1);
    int error = width + height;

    for (;;) {
        gfx_pixel(x0, y0, rgb);
        if (x0 == x1 && y0 == y1) {
            break; // reached the end
        }
        int doubled = 2 * error;
        if (doubled >= height) {
            // drifted far enough to take a step sideways
            error += height;
            x0 += step_x;
        }
        if (doubled <= width) {
            // drifted far enough to take a step up or down
            error += width;
            y0 += step_y;
        }
    }
}

// text in the console's 8 x 16 font, each font pixel drawn as a scale x scale square
void gfx_text(int x, int y, const char* text, uint32_t rgb, int scale) {
    if (scale < 1) {
        scale = 1;
    }
    for (; *text; text++, x += 8 * scale) {
        const uint8_t* glyph = font8x16[(uint8_t)*text];
        // one byte per row of the letter, bit 7 the leftmost pixel. Only the set bits are drawn,
        // so the text has no background and whatever is behind it shows through
        for (int row = 0; row < 16; row++) {
            for (int col = 0; col < 8; col++) {
                if (glyph[row] & (0x80 >> col)) {
                    gfx_rect(x + col * scale, y + row * scale, scale, scale, rgb);
                }
            }
        }
    }
}

// ---- drawing a block of pixels, scaled (video frames, sprites) -------------------------------
//
// Nearest neighbour scaling: every pixel drawn takes the colour of the source pixel under it.
// Two things make it quick enough for 25 or 30 video frames a second:
//
//   1. Which source column each column on the screen comes from is worked out once per call
//      (source_column[]), instead of a division for every pixel.
//   2. When the picture is made bigger, neighbouring screen rows often come from the same source
//      row. Then the row just drawn is copied with memcpy instead of being worked out again.
//
// On the usual kind of screen (32 bits a pixel, red-green-blue in the same order as 0x00RRGGBB)
// a source pixel can be written straight to the screen; any other kind goes through pack().

#define MOST_COLUMNS 16384
static uint32_t source_column[MOST_COLUMNS];

// is the screen 32 bit 0x00RRGGBB, the same layout as the pixels handed in?
static bool screen_is_xrgb (void) {
    return fb_bytes == 4 && fb.red_pos == 16 && fb.green_pos == 8 && fb.blue_pos == 0 &&
           fb.red_size == 8 && fb.green_size == 8 && fb.blue_size == 8;
}

// draw a w x h block of 0x00RRGGBB pixels scaled to out_w x out_h at x, y (see above)
void gfx_draw_pixels(const uint32_t* pixels, int w, int h, int stride, int x, int y, int out_w, int out_h) {
    if (!active || pixels == NULL || w <= 0 || h <= 0 || out_w <= 0 || out_h <= 0) {
        return;
    }
    // source_column[] only has room for this many (far wider than any screen)
    if (out_w > MOST_COLUMNS) {
        out_w = MOST_COLUMNS;
    }

    // the part of the scaled picture that lands on the screen, in picture coordinates
    // (column 0 being the picture's left edge on the screen, wherever that is)
    int first_column = 0;
    int last_column = out_w;      // one past the last
    int first_row = 0;
    int last_row = out_h;
    if (x < 0) {
        first_column = -x;
    }
    if (y < 0) {
        first_row = -y;
    }
    if (x + out_w > (int)fb.width) {
        last_column = (int)fb.width - x;
    }
    if (y + out_h > (int)fb.height) {
        last_row = (int)fb.height - y;
    }
    if (first_column >= last_column || first_row >= last_row) {
        return; // all of it is off the screen
    }

    // screen column `column` of out_w shows source column column * w / out_w of w: the same
    // fraction of the way across. Multiplying first keeps it exact, e.g. drawing 320 pixels at
    // 960 wide gives columns 0, 0, 0, 1, 1, 1, 2...
    for (int column = first_column; column < last_column; column++) {
        source_column[column] = (uint32_t)column * (uint32_t)w / (uint32_t)out_w;
    }

    bool straight = screen_is_xrgb();
    // the bytes of one drawn row on the screen, for copying a row that repeats
    uint32_t row_bytes = (uint32_t)(last_column - first_column) * fb_bytes;
    int previous_source_row = -1; // no row drawn yet, and -1 matches no real source row
    uint8_t* previous_row = NULL;

    for (int row = first_row; row < last_row; row++) {
        // the source row, worked out the same way as the columns
        int source_row = (int)((uint32_t)row * (uint32_t)h / (uint32_t)out_h);
        // where this row starts on the screen (or in the back buffer): its line, then the first
        // column that is on the screen
        uint8_t* screen_row = target + (uint32_t)(y + row) * target_pitch + (uint32_t)(x + first_column) * fb_bytes;

        if (source_row == previous_source_row) {
            memcpy(screen_row, previous_row, row_bytes); // the same as the row above
            continue;
        }

        // a new source row: pick out its pixels column by column
        const uint32_t* source = pixels + source_row * stride;
        if (straight) {
            // the screen's own format, so each pixel is copied as it is (the top byte is
            // ignored by the screen)
            uint32_t* out = (uint32_t*)screen_row;
            for (int column = first_column; column < last_column; column++) {
                *out++ = source[source_column[column]];
            }
        } else {
            // any other format: repacked a pixel at a time (slower, but rare)
            for (int column = first_column; column < last_column; column++) {
                uint32_t pixel = pack(source[source_column[column]]);
                put((uint32_t)(x + column), (uint32_t)(y + row), pixel);
            }
        }
        previous_source_row = source_row;
        previous_row = screen_row;
    }
}

// ---- BMP pictures -----------------------------------------------------------------------------
//
// The header fields read here (all little endian):
//     offset  0  "BM"                      offset 22  height (negative = rows stored top down)
//     offset 10  where the pixels start    offset 28  bits per pixel (8, 24 or 32 here)
//     offset 14  info header size (40+)    offset 30  compression (0, or 3 for 32 bit)
//     offset 18  width                     offset 46  palette entries (8 bit, 0 means 256)
// The palette (8 bit pictures only) follows the info header, 4 bytes per colour: blue, green,
// red, unused.

// a picture checked by bmp_open, with everything needed to find any pixel
typedef struct {
    const uint8_t* data;
    int width;
    int height;
    bool top_down;
    uint32_t bpp;
    uint32_t pixels;  // where the pixel rows start
    uint32_t stride;  // bytes per row, including the padding
    const uint8_t* palette;
    uint32_t colours; // entries in the palette
} bmp_t;

// a 16 bit little endian number at p: the first byte is the low one. Read a byte at a time, as
// BMP fields aren't always lined up on a 2 or 4 byte boundary
static uint32_t u16(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}

// a 32 bit little endian number at p: two 16 bit halves, the low half first
static uint32_t u32(const uint8_t* p) {
    return u16(p) | (u16(p + 2) << 16);
}

// read and check the headers, false for anything that isn't a BMP we can draw, or that claims
// more pixels than the file holds (so a damaged file can't make us read past its end)
static bool bmp_open(const uint8_t* d, uint32_t length, bmp_t* b) {
    // 54 bytes: the 14 byte file header and the smallest (40 byte) info header
    if (d == 0 || length < 54 || d[0] != 'B' || d[1] != 'M') {
        return false;
    }
    // the fields (see the table above). Width and height are signed, as a negative height
    // means the rows are stored top down
    uint32_t header = u32(d + 14);
    int32_t width = (int32_t)u32(d + 18);
    int32_t height = (int32_t)u32(d + 22);
    uint32_t compression = u32(d + 30);
    b->data = d;
    b->bpp = u16(d + 28);
    b->pixels = u32(d + 10);
    b->top_down = height < 0;
    b->width = width;
    b->height = height < 0 ? -height : height;

    // older (12 byte) headers are a different layout, and anything over 16384 pixels is more
    // likely a damaged file than a picture (it also keeps the sums in gfx_image within 32 bits)
    if (header < 40 || width <= 0 || width > 16384 || b->height == 0 || b->height > 16384) {
        return false;
    }
    if (b->bpp != 8 && b->bpp != 24 && b->bpp != 32) {
        return false;
    }
    // 0 = plain, 3 = "bitfields", which for 32 bit pictures is the usual blue, green, red, alpha order
    if (compression != 0 && !(compression == 3 && b->bpp == 32)) {
        return false;
    }

    // each row is padded to a whole number of 4 byte words: the bits in a row, rounded up to
    // a multiple of 32 (adding 31 before dividing), as bytes. e.g. 3 pixels at 24 bits = 72 bits,
    // which rounds up to 96 bits = 12 bytes
    b->stride = (((uint32_t)width * b->bpp + 31) / 32) * 4;
    // all the rows must be in the file. Worked out in 64 bits, as a damaged header could make
    // the product wrap round in 32 bits and look small (multiplying is fine, it's only 64 bit
    // division that needs libgcc)
    if ((uint64_t)b->pixels + (uint64_t)b->stride * (uint32_t)b->height > length) {
        return false;
    }

    // an 8 bit picture's pixels are palette numbers: find the palette, and check it is all there
    if (b->bpp == 8) {
        b->colours = u32(d + 46) ? u32(d + 46) : 256;
        b->palette = d + 14 + header; // straight after the info header, whatever size that is
        if (b->colours > 256 || 14 + header + b->colours * 4 > length) {
            return false;
        }
    }
    return true;
}

// the colour of one pixel as 0xRRGGBB, (0, 0) being the top left
static uint32_t bmp_pixel(const bmp_t* b, int x, int y) {
    // most BMPs store the bottom row first, so row y from the top is that many rows from the end
    int row = b->top_down ? y : b->height - 1 - y;
    const uint8_t* p = b->data + b->pixels + (uint32_t)row * b->stride;
    if (b->bpp == 8) {
        // a palette number: point at its colour in the palette instead
        uint32_t index = p[x];
        if (index >= b->colours) {
            return 0; // past the end of the palette (a damaged file): black rather than garbage
        }
        p = b->palette + index * 4;
    } else {
        p += (uint32_t)x * (b->bpp / 8); // 3 or 4 bytes a pixel (a 32 bit pixel's 4th is alpha)
    }
    // pixels and palette entries alike are blue, green, red: turn them round into 0xRRGGBB
    return ((uint32_t)p[2] << 16) | ((uint32_t)p[1] << 8) | p[0];
}

// ---- drawing pictures: BMP, and PNG, GIF and JPEG decoded by picture.c ------------------------

// a picture being drawn: a BMP read straight from its file, or decoded pixels (picture.h)
typedef struct {
    int width;
    int height;
    const bmp_t* bmp;        // the BMP, or NULL for decoded pixels
    const uint32_t* pixels;  // decoded pixels, 0xAARRGGBB, a row at a time
} source_t;

// the colour of pixel x, y of a picture being drawn, as 0xAARRGGBB (a BMP's are all solid)
static uint32_t source_pixel(const source_t* s, int x, int y) {
    if (s->bmp != NULL) {
        return 0xFF000000u | bmp_pixel(s->bmp, x, y);
    }
    return s->pixels[(uint32_t)y * (uint32_t)s->width + (uint32_t)x];
}

// read the pixel at x, y back off the screen (or the back buffer) as 0xRRGGBB: pack() undone
static uint32_t get(uint32_t x, uint32_t y) {
    uint8_t* p = target + y * target_pitch + x * fb_bytes;
    uint32_t pixel;
    switch (fb_bytes) {
        case 4:
            pixel = *(uint32_t*)p;
            break;
        case 3:
            pixel = p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16);
            break;
        default:
            pixel = *(uint16_t*)p;
            break;
    }
    // each colour's bits, moved back to the top of a byte
    uint32_t r = ((pixel >> fb.red_pos) & ((1u << fb.red_size) - 1)) << (8 - fb.red_size);
    uint32_t g = ((pixel >> fb.green_pos) & ((1u << fb.green_size) - 1)) << (8 - fb.green_size);
    uint32_t b = ((pixel >> fb.blue_pos) & ((1u << fb.blue_size) - 1)) << (8 - fb.blue_size);
    return (r << 16) | (g << 8) | b;
}

// a see-through colour (0xAARRGGBB) over the colour under it (0xRRGGBB): each of red, green and
// blue is that much of the top colour and the rest of the one under it
static uint32_t mix(uint32_t over, uint32_t under, uint32_t alpha) {
    uint32_t result = 0;
    for (int shift = 0; shift <= 16; shift += 8) {
        uint32_t top = (over >> shift) & 0xFF, bottom = (under >> shift) & 0xFF;
        result |= ((top * alpha + bottom * (255 - alpha) + 127) / 255) << shift;
    }
    return result;
}

// the size of a picture (BMP, PNG, GIF or JPEG), false if it isn't one we can show
bool gfx_image_size(const uint8_t* data, uint32_t length, int* w, int* h) {
    bmp_t b;
    if (bmp_open(data, length, &b)) {
        *w = b.width;
        *h = b.height;
        return true;
    }
    return picture_size(data, length, w, h);
}

// get a picture ready to draw into *s: a BMP is read straight from its file (b holds its
// details), the others are decoded into pixels first. False if it isn't a picture we can show
static bool source_open(const uint8_t* data, uint32_t length, bmp_t* b, source_t* s) {
    s->bmp = NULL;
    s->pixels = NULL;
    if (bmp_open(data, length, b)) {
        s->width = b->width;
        s->height = b->height;
        s->bmp = b;
        return true;
    }
    const picture_t* p = picture_decode(data, length);
    if (p == NULL) {
        return false;
    }
    s->width = p->width;
    s->height = p->height;
    s->pixels = p->pixels;
    return true;
}

// draw a picture (BMP, PNG, GIF or JPEG) at x, y, scaled to fit in max_w x max_h when those are above 0
bool gfx_image(const uint8_t* data, uint32_t length, int x, int y, int max_w, int max_h) {
    if (!active) {
        return false;
    }
    bmp_t b;
    source_t s;
    if (!source_open(data, length, &b, &s)) {
        return false;
    }

    // the size to draw at: as it is, or as big as fits in the box while keeping its shape.
    // The box can be far bigger than the screen (a zoomed in picture, only part of which shows).
    // Pictures and boxes are both at most 16384 pixels across, so every product below stays under
    // 2^28 and fits in 32 bits (64 bit division would need libgcc, which the kernel doesn't link)
    uint32_t w = (uint32_t)s.width;
    uint32_t h = (uint32_t)s.height;
    if (max_w > 0 && max_h > 0) {
        if (max_w > 16384) {
            max_w = 16384;
        }
        if (max_h > 16384) {
            max_h = 16384;
        }
        // compare the shapes without dividing: width / height <= max_w / max_h is the same as
        // width * max_h <= height * max_w. If the picture is the narrower shape, its height
        // fills the box and the width follows; otherwise the width fills it
        if ((uint32_t)s.width * (uint32_t)max_h <= (uint32_t)s.height * (uint32_t)max_w) {
            h = (uint32_t)max_h;
            w = (uint32_t)s.width * (uint32_t)max_h / (uint32_t)s.height;
        } else {
            w = (uint32_t)max_w;
            h = (uint32_t)s.height * (uint32_t)max_w / (uint32_t)s.width;
        }
        // a very long thin picture could round down to 0 one way, and then draw nothing
        if (w < 1) {
            w = 1;
        }
        if (h < 1) {
            h = 1;
        }
    }

    // only the part of the picture that lands on the screen is worth drawing: tx and ty run over
    // the drawn picture, so start where it comes onto the screen and stop where it goes off.
    // A zoomed in picture can be thousands of pixels wider than the screen
    uint32_t x0 = x < 0 ? (uint32_t)-x : 0, x1 = w;
    uint32_t y0 = y < 0 ? (uint32_t)-y : 0, y1 = h;
    // starts past the right or bottom edge: none of it shows, but it is still a good picture
    if (x >= (int)fb.width || y >= (int)fb.height) {
        return true;
    }
    if (x + (int)w > (int)fb.width) {
        x1 = (uint32_t)((int)fb.width - x);
    }
    if (y + (int)h > (int)fb.height) {
        y1 = (uint32_t)((int)fb.height - y);
    }

    // nearest neighbour: every screen pixel takes the colour of the picture pixel under it
    // (pixel tx of w comes from pixel tx * width / w of the picture, the same fraction across).
    // See-through pixels are left as they are, and partly see-through ones mixed with them
    for (uint32_t ty = y0; ty < y1; ty++) {
        int py = (int)(ty * (uint32_t)s.height / h);
        uint32_t sy = (uint32_t)(y + (int)ty);
        for (uint32_t tx = x0; tx < x1; tx++) {
            int px = (int)(tx * (uint32_t)s.width / w);
            uint32_t sx = (uint32_t)(x + (int)tx);
            uint32_t colour = source_pixel(&s, px, py);
            uint32_t alpha = colour >> 24;
            if (alpha == 0) {
                continue;
            }
            if (alpha < 255) {
                colour = mix(colour, get(sx, sy), alpha);
            }
            put(sx, sy, pack(colour & 0xFFFFFF));
        }
    }
    return true;
}

// ---- textures: pictures stretched over a strip or a band, for ray casting games -----------------
//
// A ray casting game (like the maze3d package) draws each wall a narrow strip at a time, and
// its floor and ceiling a band of rows at a time. Covering them with a picture of stone (a
// "texture") means working out a colour for every pixel, which is far too slow in AerLang, so
// these do a whole strip or band in one call:
//
//   gfx_texture_column  a strip w x h: across it, the picture's columns from u0 to u1, and down
//                       it the picture's whole height, stretched to fit (a piece of wall)
//   gfx_texture_row     a band w x h: every row the same, the picture read along the line from
//                       (u0, v0) at the left to (u1, v1) at the right (a stretch of floor)
//
// Both darken the picture by `light` (1 is as it is, 0 black), which is how a ray caster makes
// far walls dark, and both repeat the picture past its edges (so the floor is tiled with it).
// gfx_texture_light can also shine a round spotlight on them, like a flashlight: then they
// are lit only inside a circle on the screen, brightest in its middle, and black outside it.
//
// Each row of a strip or band is drawn in two steps: its pixels are read from the picture into
// colours[] (sample_line, or the columns loop in gfx_texture_column), then darkened and put on
// the screen (light_row). The rows of a band are all copies of its first. In a strip, a row
// that shows the same picture row as the one above is a copy of it too, unless the spotlight
// is on, as it lights each row differently.
//
// Positions in the picture are kept "16.16 fixed point": pixels times 65536, so the bottom 16
// bits are the fraction of a pixel. Adding a step for each pixel drawn walks across the picture
// with whole numbers only, which is much quicker here than decimals. A position always stays
// between 0 and the picture's size, which is at most 16384 * 65536 = 2^30, so adding a step to
// it (also under 2^30) can't overflow 32 bits.

// one row of a strip or band, read from the picture but not yet darkened (never wider than the
// screen, as only the part on the screen is read)
static uint32_t colours[MOST_COLUMNS];

// the light as a whole number from 0 to 256, for light_row()
static uint32_t light_level(double light) {
    if (light <= 0) {
        return 0;
    }
    if (light >= 1) {
        return 256;
    }
    return (uint32_t)(light * 256);
}

// a position in pixels (any number, even negative or past the edge) as a 16.16 fixed point one
// inside a picture `size` pixels across, the way the picture repeats: e.g. 70 in a picture 64
// across is 6, and -1 is 63
static uint32_t wrap(double at, int size) {
    double inside = at - floor(at / size) * size;
    uint32_t fixed = (uint32_t)(inside * 65536);
    // rounding can land it exactly on the edge, which is the start again
    return fixed >= (uint32_t)size << 16 ? 0 : fixed;
}

// Reading a picture's pixels quickly. A 24 or 32 bit BMP keeps each pixel as blue, green, red
// bytes (and a 4th for 32 bit), and so do decoded pictures (0xAARRGGBB, stored lowest byte
// first). For those, a pixel is found from where the top row starts and how far apart the rows
// are, much quicker than asking source_pixel() for each one. (8 bit BMPs, with a palette, still
// go through source_pixel)
typedef struct {
    const source_t* source;   // the picture
    const uint8_t* top;       // the first byte of its top row, or NULL to use source_pixel
    int32_t row_step;         // bytes from one row to the row below (negative if stored bottom up)
    uint32_t bytes;           // bytes a pixel: 3 or 4
} texture_t;

// get a picture opened by source_open ready for reading quickly (see texture_t)
static void texture_open(const source_t* s, texture_t* t) {
    t->source = s;
    t->top = NULL;
    if (s->bmp == NULL) {
        t->top = (const uint8_t*)s->pixels;
        t->row_step = s->width * 4;
        t->bytes = 4;
    } else if (s->bmp->bpp != 8) {
        const bmp_t* b = s->bmp;
        // most BMPs keep their bottom row first, so their top row is the last one stored
        uint32_t top_row = b->top_down ? 0 : (uint32_t)b->height - 1;
        t->top = b->data + b->pixels + top_row * b->stride;
        t->row_step = b->top_down ? (int32_t)b->stride : -(int32_t)b->stride;
        t->bytes = b->bpp / 8;
    }
}

// the colour 0xRRGGBB of pixel x, y of a texture. A 4 byte pixel is read in one go (its 4th
// byte, alpha, is dropped), a 3 byte one a byte at a time
static uint32_t texel(const texture_t* t, uint32_t x, uint32_t y) {
    if (t->top == NULL) {
        return source_pixel(t->source, (int)x, (int)y) & 0xFFFFFF;
    }
    const uint8_t* p = t->top + (int32_t)y * t->row_step + x * t->bytes;
    if (t->bytes == 4) {
        return *(const uint32_t*)p & 0xFFFFFF;
    }
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16);
}

// read `count` pixels of a texture into colours[], along a line: from the 16.16 position u, v,
// a step of u_step, v_step at a time, going round the picture's edges (wrapped positions, see
// wrap). This is most of the work of drawing a floor, so the usual kind of picture (texture.top
// set) has its own loop, with texel() written out in it
static void sample_line(const texture_t* t, uint32_t u, uint32_t v, uint32_t u_step, uint32_t v_step, int count) {
    uint32_t size_across = (uint32_t)t->source->width << 16, size_down = (uint32_t)t->source->height << 16;
    for (int i = 0; i < count; i++) {
        if (t->top != NULL) {
            const uint8_t* p = t->top + (int32_t)(v >> 16) * t->row_step + (u >> 16) * t->bytes;
            colours[i] = t->bytes == 4 ? *(const uint32_t*)p : (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16);
        } else {
            colours[i] = texel(t, u >> 16, v >> 16);
        }
        u += u_step;
        if (u >= size_across) {
            u -= size_across;
        }
        v += v_step;
        if (v >= size_down) {
            v -= size_down;
        }
    }
}

// how much of `level` (0 to 256) the spotlight lets through at x, y: all of it with the
// spotlight off, none outside its circle, and spot_table[] inside it
static uint32_t spot_level(int x, int y, uint32_t level) {
    if (spot_r2 == 0) {
        return level;
    }
    int dx = x - spot_x, dy = y - spot_y;
    uint32_t d2 = (uint32_t)(dx * dx) + (uint32_t)(dy * dy);
    return (level * (d2 < spot_r2 ? spot_table[(d2 * spot_scale) >> 16] : spot_outside)) >> 8;
}

// put the first `count` of colours[] on the screen from x, y (all on the screen), each darkened
// by `level` (0 to 256) and by the spotlight, if it is on. The spotlight is worked out as if
// the row were at lit_y (the middle of a band, whose other rows are copies of this one), and
// either for each pixel across the row (across, for a floor) or once at its middle (for a
// narrow strip of wall, where it hardly changes from one side to the other).
// Darkening multiplies each of red, green and blue by level / 256: red and blue in one
// multiply, as each has 8 empty bits above it to hold its result, then green. (A colour's top
// byte may hold a picture's alpha: the masks drop it.) This runs for most pixels of a frame,
// so the usual kinds of screen and light each have a loop of their own, with nothing else
// worked out in it
static void light_row(int x, int y, int count, uint32_t level, int lit_y, bool across) {
    bool spot_across = spot_r2 != 0 && across;
    if (!spot_across) {
        level = spot_level(x + count / 2, lit_y, level);
    }
    if (!screen_is_xrgb()) {
        for (int i = 0; i < count; i++) {
            uint32_t lit = spot_across ? spot_level(x + i, lit_y, level) : level;
            uint32_t c = colours[i];
            put((uint32_t)(x + i), (uint32_t)y, pack((((c & 0xFF00FF) * lit >> 8) & 0xFF00FF) | (((c & 0x00FF00) * lit >> 8) & 0x00FF00)));
        }
        return;
    }
    uint32_t* out = (uint32_t*)(target + (uint32_t)y * target_pitch) + x;
    if (!spot_across) {
        for (int i = 0; i < count; i++) {
            uint32_t c = colours[i];
            out[i] = (((c & 0xFF00FF) * level >> 8) & 0xFF00FF) | (((c & 0x00FF00) * level >> 8) & 0x00FF00);
        }
        return;
    }
    // the spotlight for each pixel: its distance from the middle, squared, goes up by
    // 2 * dx + 1 from one pixel to the next, so no multiplying is needed for that
    int dx = x - spot_x, dy = lit_y - spot_y;
    uint32_t d2 = (uint32_t)(dx * dx) + (uint32_t)(dy * dy);
    for (int i = 0; i < count; i++, d2 += (uint32_t)(2 * dx + 1), dx++) {
        uint32_t lit = (level * (d2 < spot_r2 ? spot_table[(d2 * spot_scale) >> 16] : spot_outside)) >> 8;
        uint32_t c = colours[i];
        out[i] = (((c & 0xFF00FF) * lit >> 8) & 0xFF00FF) | (((c & 0x00FF00) * lit >> 8) & 0x00FF00);
    }
}

// sample_line and light_row in one go, for the floor's sake: the usual screen (0x00RRGGBB) and
// picture (4 bytes a pixel, so each is read in one go) only. `count` pixels of the line from
// u, v (stepping as in sample_line) go straight onto the screen from x, y, darkened by `level`
// and by the spotlight as if the row were at lit_y (as in light_row). Without the second pass
// over colours[], a floor is drawn about a tenth quicker
static void straight_line(const texture_t* t, uint32_t u, uint32_t v, uint32_t u_step, uint32_t v_step, int x, int y, int count, uint32_t level, int lit_y) {
    uint32_t size_across = (uint32_t)t->source->width << 16, size_down = (uint32_t)t->source->height << 16;
    uint32_t* out = (uint32_t*)(target + (uint32_t)y * target_pitch) + x;
    // the spotlight's distance squared, as in light_row
    int dx = x - spot_x, dy = lit_y - spot_y;
    uint32_t d2 = (uint32_t)(dx * dx) + (uint32_t)(dy * dy);
    for (int i = 0; i < count; i++) {
        uint32_t c = *(const uint32_t*)(t->top + (int32_t)(v >> 16) * t->row_step + (u >> 16) * 4);
        uint32_t lit = level;
        if (spot_r2 != 0) {
            lit = (level * (d2 < spot_r2 ? spot_table[(d2 * spot_scale) >> 16] : spot_outside)) >> 8;
            d2 += (uint32_t)(2 * dx + 1);
            dx++;
        }
        out[i] = (((c & 0xFF00FF) * lit >> 8) & 0xFF00FF) | (((c & 0x00FF00) * lit >> 8) & 0x00FF00);
        u += u_step;
        if (u >= size_across) {
            u -= size_across;
        }
        v += v_step;
        if (v >= size_down) {
            v -= size_down;
        }
    }
}

// shine a spotlight on textures: a circle round x, y on the screen. It is fully lit out to a
// third of the radius, then fades smoothly to `ambient` (0 to 1: 0 is black) at its edge, and
// everything outside it is lit by `ambient` too, like the faint light of a room you can just
// make things out in. A radius of 0 or less turns it off. (The sums in light_row stay within
// 32 bits for any radius up to 16384)
void gfx_texture_light(int x, int y, int radius, double ambient) {
    spot_r2 = 0;
    if (radius <= 0) {
        return;
    }
    if (radius > 16384) {
        radius = 16384;
    }
    spot_x = x;
    spot_y = y;
    spot_r2 = (uint32_t)radius * (uint32_t)radius;
    // any distance squared under spot_r2, times this, is under 256 << 16
    spot_scale = (256u << 16) / spot_r2;
    spot_outside = light_level(ambient);
    // a game usually moves its spotlight every frame, but seldom changes its size: the table
    // only depends on the size and the ambient light, so it is kept until one of them changes
    if (radius == table_radius && spot_outside == table_ambient) {
        return;
    }
    table_radius = radius;
    table_ambient = spot_outside;
    for (int i = 0; i < 256; i++) {
        // how far out entry i is, from 0 in the middle to 1 at the edge
        double out = sqrt((i + 0.5) / 256);
        double light = 1;
        if (out > 1.0 / 3) {
            // "smoothstep": 3s^2 - 2s^3 goes from 0 to 1 with no sudden change at either end
            double s = (out - 1.0 / 3) * 1.5;
            light = 1 - s * s * (3 - 2 * s);
        }
        uint32_t level = (uint32_t)(light * 256);
        spot_table[i] = (uint16_t)(level > spot_outside ? level : spot_outside);
    }
}

// light_row for a see-through picture (a sprite): the same, but a pixel whose alpha (the top
// byte of its colour in colours[]) is under 128 is left as it is, so what was drawn behind it
// shows through. Only for strips, so the spotlight is worked out once, at the strip's middle
static void light_row_see_through(int x, int y, int count, uint32_t level, int lit_y) {
    level = spot_level(x + count / 2, lit_y, level);
    // on the usual screen (0x00RRGGBB) straight into the row, as in light_row
    uint32_t* out = screen_is_xrgb() ? (uint32_t*)(target + (uint32_t)y * target_pitch) + x : NULL;
    for (int i = 0; i < count; i++) {
        uint32_t c = colours[i];
        if ((c >> 24) < 128) {
            continue;
        }
        uint32_t lit = (((c & 0xFF00FF) * level >> 8) & 0xFF00FF) | (((c & 0x00FF00) * level >> 8) & 0x00FF00);
        if (out != NULL) {
            out[i] = lit;
        } else {
            put((uint32_t)(x + i), (uint32_t)y, pack(lit));
        }
    }
}

// a strip of wall: a picture stretched over w x h at x, y (see the top of this part). With
// see_through, the picture's see-through pixels are skipped, for a sprite drawn a strip at a time
// (only pictures with an alpha byte have any: 32 bit BMPs, and PNGs and GIFs)
bool gfx_texture_column(const uint8_t* data, uint32_t length, int x, int y, int w, int h, double u0, double u1, double light, bool see_through) {
    if (!active) {
        return false;
    }
    bmp_t b;
    source_t s;
    if (!source_open(data, length, &b, &s)) {
        return false;
    }
    // the part of the strip on the screen, as in gfx_draw_pixels: columns and rows of the strip
    int first_column = x < 0 ? -x : 0, last_column = w;
    int first_row = y < 0 ? -y : 0, last_row = h;
    if (x + w > (int)fb.width) {
        last_column = (int)fb.width - x;
    }
    if (y + h > (int)fb.height) {
        last_row = (int)fb.height - y;
    }
    if (first_column >= last_column || first_row >= last_row) {
        return true; // none of it shows, but it is still a good picture
    }
    int count = last_column - first_column;
    int left = x + first_column;

    // Which picture column each screen column shows, worked out once (in source_column[], which
    // gfx_draw_pixels uses the same way, here counted from the first column on the screen).
    // Each is read at the middle of its screen pixel
    uint32_t size_across = (uint32_t)s.width << 16;
    double step = (u1 - u0) / w;
    uint32_t u = wrap(u0 + step * (first_column + 0.5), s.width);
    uint32_t u_step = wrap(step, s.width);
    for (int i = 0; i < count; i++) {
        source_column[i] = u >> 16;
        u += u_step;
        if (u >= size_across) {
            u -= size_across;
        }
    }

    // Down the strip, the picture's rows from top to bottom: h screen rows for s.height picture
    // rows. A wall right in front of you can be far taller than the screen, so this starts at
    // the first row that is on it
    double v_step = (double)s.height / h;
    uint32_t v = (uint32_t)((first_row + 0.5) * v_step * 65536);
    uint32_t v_fixed_step = (uint32_t)(v_step * 65536);
    uint32_t level = light_level(light);
    texture_t texture;
    texture_open(&s, &texture);
    uint32_t row_bytes = (uint32_t)count * fb_bytes;
    uint32_t black = pack(0);
    // a picture with an alpha byte (see texture_t: 4 bytes a pixel). Others are solid all over
    bool has_alpha = texture.top != NULL && texture.bytes == 4;
    int previous_source_row = -1;
    uint8_t* previous_row = NULL;
    for (int row = first_row; row < last_row; row++, v += v_fixed_step) {
        int source_row = (int)(v >> 16);
        if (source_row >= s.height) {
            source_row = s.height - 1; // rounding at the very bottom
        }
        int screen_y = y + row;
        uint8_t* screen_row = target + (uint32_t)screen_y * target_pitch + (uint32_t)left * fb_bytes;
        // a row of wall wholly above or below the spotlight is black (unless there is an
        // ambient light). A sprite's row is drawn anyway: its solid parts black, its see-through
        // parts not at all, so it hides what is behind it as a dark shape
        if (spot_r2 != 0 && spot_outside == 0 && !see_through &&
            (uint32_t)((screen_y - spot_y) * (screen_y - spot_y)) >= spot_r2) {
            fill_row((uint32_t)left, (uint32_t)screen_y, (uint32_t)count, black);
            continue;
        }
        // a wall nearer than the picture is tall shows each picture row on several screen rows:
        // the same colours again, or (without the spotlight, and for a solid picture) a copy of
        // the row above. A sprite's row can't be copied, as what shows through it differs
        if (source_row != previous_source_row) {
            if (texture.top != NULL) {
                // the usual kind of picture, with texel() written out
                const uint8_t* picture_row = texture.top + source_row * texture.row_step;
                for (int i = 0; i < count; i++) {
                    const uint8_t* p = picture_row + source_column[i] * texture.bytes;
                    colours[i] = texture.bytes == 4 ? *(const uint32_t*)p : (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16);
                }
            } else {
                // texel() drops the alpha byte: these pictures are solid
                for (int i = 0; i < count; i++) {
                    colours[i] = texel(&texture, source_column[i], (uint32_t)source_row) | 0xFF000000u;
                }
            }
            if (see_through && !has_alpha) {
                // a 24 bit picture has no alpha byte, so it is solid all over
                for (int i = 0; i < count; i++) {
                    colours[i] |= 0xFF000000u;
                }
            }
        } else if (spot_r2 == 0 && !see_through) {
            memcpy(screen_row, previous_row, row_bytes);
            continue;
        }
        if (see_through) {
            light_row_see_through(left, screen_y, count, level, screen_y);
        } else {
            light_row(left, screen_y, count, level, screen_y, false);
        }
        previous_source_row = source_row;
        previous_row = screen_row;
    }
    return true;
}

// a band of floor or ceiling: w x h at x, y, every row the picture read along a line (see the
// top of this part)
bool gfx_texture_row(const uint8_t* data, uint32_t length, int x, int y, int w, int h, double u0, double v0, double u1, double v1, double light) {
    if (!active) {
        return false;
    }
    bmp_t b;
    source_t s;
    if (!source_open(data, length, &b, &s)) {
        return false;
    }
    int first_column = x < 0 ? -x : 0, last_column = w;
    int first_row = y < 0 ? -y : 0, last_row = h;
    if (x + w > (int)fb.width) {
        last_column = (int)fb.width - x;
    }
    if (y + h > (int)fb.height) {
        last_row = (int)fb.height - y;
    }
    if (first_column >= last_column || first_row >= last_row) {
        return true;
    }

    // With the spotlight on, only the columns inside its circle need reading from the picture:
    // those within its half width at the band's row nearest its middle (the circle is widest
    // there). Everything else is black
    int lit_first = first_column, lit_last = last_column;
    if (spot_r2 != 0 && spot_outside == 0) {
        int nearest = spot_y;
        if (nearest < y + first_row) {
            nearest = y + first_row;
        } else if (nearest > y + last_row - 1) {
            nearest = y + last_row - 1;
        }
        uint32_t dy2 = (uint32_t)((nearest - spot_y) * (nearest - spot_y));
        lit_last = lit_first; // nothing, unless the circle reaches the band
        if (dy2 < spot_r2) {
            int half = (int)sqrt((double)(spot_r2 - dy2)) + 1;
            lit_first = spot_x - half - x > first_column ? spot_x - half - x : first_column;
            lit_last = spot_x + half + 1 - x < last_column ? spot_x + half + 1 - x : last_column;
        }
    }

    // The band's first row on the screen, lit as its middle row would be (the spotlight changes
    // little over a few rows), then the rest of the band is copies of it. Along the row, a step
    // along the line for each screen pixel, starting from the middle of the first one lit
    uint32_t top = (uint32_t)(y + first_row);
    if (spot_r2 != 0 && spot_outside == 0) {
        fill_row((uint32_t)(x + first_column), top, (uint32_t)(last_column - first_column), pack(0));
    }
    if (lit_first < lit_last) {
        texture_t texture;
        texture_open(&s, &texture);
        double step_u = (u1 - u0) / w, step_v = (v1 - v0) / w;
        uint32_t u = wrap(u0 + step_u * (lit_first + 0.5), s.width), v = wrap(v0 + step_v * (lit_first + 0.5), s.height);
        uint32_t u_step = wrap(step_u, s.width), v_step = wrap(step_v, s.height);
        int count = lit_last - lit_first, lit_y = y + (first_row + last_row) / 2;
        if (screen_is_xrgb() && texture.top != NULL && texture.bytes == 4) {
            straight_line(&texture, u, v, u_step, v_step, x + lit_first, (int)top, count, light_level(light), lit_y);
        } else {
            sample_line(&texture, u, v, u_step, v_step, count);
            light_row(x + lit_first, (int)top, count, light_level(light), lit_y, true);
        }
    }
    uint8_t* first = target + top * target_pitch + (uint32_t)(x + first_column) * fb_bytes;
    uint32_t row_bytes = (uint32_t)(last_column - first_column) * fb_bytes;
    for (int row = first_row + 1; row < last_row; row++) {
        memcpy(first + (uint32_t)(row - first_row) * target_pitch, first, row_bytes);
    }
    return true;
}

// ---- effects over what is drawn: a tint, and noise ----------------------------------------------
//
// Both change pixels already drawn, for effects a game puts over its whole picture: fading to
// black (a tint of black, a little more each frame), a red flash when something hurts you, or
// the grainy "static" of a picture seen in the dark. Each works on a rectangle, cut to fit on
// the screen first (as gfx_rect does).

// cut a rectangle to fit on the screen, false if none of it is on it
static bool clip_area(int* x, int* y, int* w, int* h) {
    if (*x < 0) {
        *w += *x;
        *x = 0;
    }
    if (*y < 0) {
        *h += *y;
        *y = 0;
    }
    if (*x + *w > (int)fb.width) {
        *w = (int)fb.width - *x;
    }
    if (*y + *h > (int)fb.height) {
        *h = (int)fb.height - *y;
    }
    return *w > 0 && *h > 0;
}

// mix every pixel of a w x h area at x, y with the colour 0xRRGGBB: `amount` of the way
// there (0 leaves it as it is, 1 is all that colour)
void gfx_tint(int x, int y, int w, int h, uint32_t rgb, double amount) {
    if (!active || !clip_area(&x, &y, &w, &h)) {
        return;
    }
    // how much of the colour (a) and of the pixel (256 - a), out of 256. Red and blue are
    // worked out together, as in light_row: the two weights add up to 256, so neither can
    // spill into the colour above it
    uint32_t a = light_level(amount);
    uint32_t keep = 256 - a;
    uint32_t add_rb = (rgb & 0xFF00FF) * a, add_g = (rgb & 0x00FF00) * a;
    for (int row = 0; row < h; row++) {
        if (screen_is_xrgb()) {
            uint32_t* out = (uint32_t*)(target + (uint32_t)(y + row) * target_pitch) + x;
            for (int i = 0; i < w; i++) {
                uint32_t c = out[i];
                out[i] = ((((c & 0xFF00FF) * keep + add_rb) >> 8) & 0xFF00FF) | ((((c & 0x00FF00) * keep + add_g) >> 8) & 0x00FF00);
            }
        } else {
            for (int i = 0; i < w; i++) {
                uint32_t c = get((uint32_t)(x + i), (uint32_t)(y + row));
                put((uint32_t)(x + i), (uint32_t)(y + row),
                    pack(((((c & 0xFF00FF) * keep + add_rb) >> 8) & 0xFF00FF) | ((((c & 0x00FF00) * keep + add_g) >> 8) & 0x00FF00)));
            }
        }
    }
}

// a byte of red, green or blue moved by `by` (which may be negative), kept between 0 and 255
static uint32_t nudge(uint32_t byte, int by) {
    int moved = (int)byte + by;
    return moved < 0 ? 0 : moved > 255 ? 255 : (uint32_t)moved;
}

// the last of a run of random looking numbers, for gfx_noise (see next_noise)
static uint32_t noise_seed = 2463534242u;

// the next of that run: "xorshift", which mixes the bits of the last number with shifted copies
// of itself. Quick, and plenty random for grain
static uint32_t next_noise(void) {
    noise_seed ^= noise_seed << 13;
    noise_seed ^= noise_seed >> 17;
    noise_seed ^= noise_seed << 5;
    return noise_seed;
}

// grain over a w x h area at x, y: every pixel made lighter or darker by a random amount, up to
// `amount` (0 to 255) either way, the same for red, green and blue, so it looks like the
// static of a picture seen in the dark. Different every time, so over several frames it seethes
void gfx_noise(int x, int y, int w, int h, int amount) {
    if (!active || amount <= 0 || !clip_area(&x, &y, &w, &h)) {
        return;
    }
    if (amount > 255) {
        amount = 255;
    }
    // a random byte (0 to 255) times `spread`, >> 8, is 0 to 2 * amount: less `amount`, it is
    // from -amount to amount
    int spread = 2 * amount + 1;
    for (int row = 0; row < h; row++) {
        if (screen_is_xrgb()) {
            // the usual screen (0x00RRGGBB): straight at the pixels, no packing and unpacking
            uint32_t* out = (uint32_t*)(target + (uint32_t)(y + row) * target_pitch) + x;
            for (int i = 0; i < w; i++) {
                int by = (int)(((next_noise() & 0xFF) * (uint32_t)spread) >> 8) - amount;
                uint32_t c = out[i];
                out[i] = (nudge(c >> 16 & 0xFF, by) << 16) | (nudge((c >> 8) & 0xFF, by) << 8) | nudge(c & 0xFF, by);
            }
            continue;
        }
        for (int i = 0; i < w; i++) {
            int by = (int)(((next_noise() & 0xFF) * (uint32_t)spread) >> 8) - amount;
            uint32_t c = get((uint32_t)(x + i), (uint32_t)(y + row));
            uint32_t r = nudge(c >> 16, by), g = nudge((c >> 8) & 0xFF, by), b = nudge(c & 0xFF, by);
            put((uint32_t)(x + i), (uint32_t)(y + row), pack((r << 16) | (g << 8) | b));
        }
    }
}

// ---- overlays (see gfx_overlay_begin in gfx.h) --------------------------------------------------

struct gfx_overlay {
    int x, y, w, h;          // the area kept, already cut to fit on the screen
    uint8_t* old_target;     // where drawing went before (the screen, or the back buffer)
    uint32_t old_pitch;
    uint8_t pixels[];        // the area's pixels as they were, w * fb_bytes bytes a row
};

// keep what the screen shows in an area, and draw straight onto the screen until gfx_overlay_end
gfx_overlay_t* gfx_overlay_begin(int x, int y, int w, int h) {
    if (!active) {
        return NULL;
    }
    // only the part that is on the screen
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > (int)fb.width) { w = (int)fb.width - x; }
    if (y + h > (int)fb.height) { h = (int)fb.height - y; }
    if (w <= 0 || h <= 0) {
        return NULL;
    }

    // one allocation holds both the struct and the pixels kept, which go in its pixels[] at the end
    uint32_t row_bytes = (uint32_t)w * fb_bytes;
    gfx_overlay_t* overlay = malloc(sizeof(gfx_overlay_t) + row_bytes * (uint32_t)h);
    if (overlay == NULL) {
        return NULL;
    }
    overlay->x = x;
    overlay->y = y;
    overlay->w = w;
    overlay->h = h;

    // keep what the screen shows there now (the screen itself, not the back buffer: it is the
    // screen that has to look the same afterwards), a row at a time as the rows are pitch apart
    uint8_t* screen = (uint8_t*)(uintptr_t)fb.address;
    for (int row = 0; row < h; row++) {
        memcpy(overlay->pixels + (uint32_t)row * row_bytes,
               screen + (uint32_t)(y + row) * fb.pitch + (uint32_t)x * fb_bytes, row_bytes);
    }

    // and draw straight onto the screen until the overlay ends
    overlay->old_target = target;
    overlay->old_pitch = target_pitch;
    target = screen;
    target_pitch = fb.pitch;
    return overlay;
}

// put back what was under the overlay, and send drawing wherever it went before
void gfx_overlay_end(gfx_overlay_t* overlay) {
    if (overlay == NULL) {
        return;
    }
    uint8_t* screen = (uint8_t*)(uintptr_t)fb.address;
    uint32_t row_bytes = (uint32_t)overlay->w * fb_bytes;
    // the kept pixels back onto the screen, row by row
    for (int row = 0; row < overlay->h; row++) {
        memcpy(screen + (uint32_t)(overlay->y + row) * fb.pitch + (uint32_t)overlay->x * fb_bytes,
               overlay->pixels + (uint32_t)row * row_bytes, row_bytes);
    }
    target = overlay->old_target;
    target_pitch = overlay->old_pitch;
    free(overlay);
}
