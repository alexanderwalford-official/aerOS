#!/usr/bin/env python3
"""
make_knocks_textures.py - make the wall, floor and ceiling pictures of Four Knocks (the knocks
package): the observatory's wallpaper, wood panels, windows and carpet up top, its concrete,
pipes and tiles below, and the bare rock round the borehole

    python3 tools/make_knocks_textures.py

needs numpy, and writes them to packages/knocks/, where pkg install puts them in /apps/knocks/
next to the game (packages/knocks.aer reads them with rdb and draws them with imgcol and imgrow).
Nothing is photographed or copied: every picture is worked out here from a few simple ideas,
like tools/make_maze3d_textures.py:

    noise      "value noise": random values on a coarse grid, blended smoothly in between, at a
               few sizes added together. Grain, stains, damp and dirt all come from it
    blocks     rows of blocks or boards with gaps between them, each a little different
    bevels     an edge lit on top and shaded below, so a shape looks raised (or, the other way
               round, sunk in)
    letters    a few words (STAIRS, QUIET...) drawn with a small 5 x 7 pixel alphabet below

Every picture is 64 x 64 pixels, one square of the map, and the walls' noise wraps round its
edges so neighbouring squares join without a seam. They are 32 bit BMPs (16 KB each): AerOS
draws BMPs straight from their bytes, much quicker than decoding a PNG every frame.

Running this again makes exactly the same pictures: all the randomness comes from fixed seeds.
"""

import os
import struct

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'knocks')

SIZE = 64   # pixels across and down: one square of the map

# where every pixel is: X[y, x] is x, Y[y, x] is y (numpy works on whole pictures at once)
Y, X = np.mgrid[0:SIZE, 0:SIZE].astype(float)


# ---- helpers ------------------------------------------------------------------------------------

def noise(seed, cells, size=SIZE):
    """Smooth noise from 0 to 1 over a size x size picture, with `cells` grid squares across it.
    Each grid corner gets a random value, and the pixels between are blended with "smoothstep"
    so the grid doesn't show. It wraps round, so the picture tiles."""
    rng = np.random.default_rng(seed)
    corners = rng.random((cells, cells))
    yy, xx = np.mgrid[0:size, 0:size].astype(float) * cells / size
    x0, y0 = xx.astype(int), yy.astype(int)
    tx, ty = xx - x0, yy - y0
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
    x1, y1 = (x0 + 1) % cells, (y0 + 1) % cells
    top = corners[y0, x0] * (1 - tx) + corners[y0, x1] * tx
    bottom = corners[y1, x0] * (1 - tx) + corners[y1, x1] * tx
    return top * (1 - ty) + bottom * ty


def fractal(seed, size=SIZE, octaves=(4, 8, 16, 32)):
    """Noise at several sizes added together (each finer one half as strong): blotches with grain
    inside them, which is what most dirt and stone looks like. From 0 to 1."""
    total, weight, strength = np.zeros((size, size)), 0.0, 1.0
    for i, cells in enumerate(octaves):
        total += noise(seed * 31 + i, cells, size) * strength
        weight += strength
        strength /= 2
    return total / weight


def grain(seed, amount):
    """Fine random speckle, from -amount to amount, a different value for every pixel."""
    return (np.random.default_rng(seed).random((SIZE, SIZE)) * 2 - 1) * amount


def picture(colour):
    """A picture all one colour: an array of SIZE x SIZE pixels of [red, green, blue] (0 to 255)."""
    return np.ones((SIZE, SIZE, 3)) * np.array(colour, float)


def shade(pic, amount):
    """Each pixel made brighter or darker by `amount`, a number for each pixel (1 leaves it)."""
    return pic * amount[..., None]


def blend(pic, colour, amount):
    """Each pixel moved `amount` (0 to 1, a number for each pixel) of the way to `colour`."""
    amount = np.clip(amount, 0, 1)[..., None]
    return pic * (1 - amount) + np.array(colour, float) * amount


def save(name, pic):
    """Write a picture as a 32 bit BMP: a 54 byte header, then the rows of pixels, the bottom
    row first, each pixel blue, green, red and a 4th byte (255: solid) for AerOS's alpha."""
    pixels = np.clip(np.round(pic), 0, 255).astype(np.uint8)
    h, w = pixels.shape[:2]
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        row = np.concatenate([pixels[y, :, ::-1], np.full((w, 1), 255, np.uint8)], axis=1)
        rows += row.tobytes()
    header = struct.pack('<2sIHHI', b'BM', 54 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 32, 0, len(rows), 2835, 2835, 0, 0)
    with open(os.path.join(OUT, name + '.bmp'), 'wb') as f:
        f.write(header + info + rows)


def bevel_box(pic, x0, y0, x1, y1, light=1.25, dark=0.6, width=1):
    """Light the top and left edges of a box and shade its bottom and right ones, so it looks
    raised (swap light and dark to make it look sunk in)."""
    pic[y0:y0 + width, x0:x1 + 1] *= light
    pic[y0:y1 + 1, x0:x0 + width] *= light
    pic[y1 - width + 1:y1 + 1, x0:x1 + 1] *= dark
    pic[y0:y1 + 1, x1 - width + 1:x1 + 1] *= dark


# A small alphabet, 5 pixels wide and 7 tall: '#' is ink. Only the letters the pictures use
LETTERS = {
    'A': [' ### ', '#   #', '#   #', '#####', '#   #', '#   #', '#   #'],
    'B': ['#### ', '#   #', '#   #', '#### ', '#   #', '#   #', '#### '],
    'C': [' ####', '#    ', '#    ', '#    ', '#    ', '#    ', ' ####'],
    'D': ['#### ', '#   #', '#   #', '#   #', '#   #', '#   #', '#### '],
    'E': ['#####', '#    ', '#    ', '#### ', '#    ', '#    ', '#####'],
    'G': [' ####', '#    ', '#    ', '#  ##', '#   #', '#   #', ' ####'],
    'H': ['#   #', '#   #', '#   #', '#####', '#   #', '#   #', '#   #'],
    'I': ['#####', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '#####'],
    'K': ['#   #', '#  # ', '# #  ', '##   ', '# #  ', '#  # ', '#   #'],
    'L': ['#    ', '#    ', '#    ', '#    ', '#    ', '#    ', '#####'],
    'N': ['#   #', '##  #', '# # #', '#  ##', '#   #', '#   #', '#   #'],
    'O': [' ### ', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '],
    'Q': [' ### ', '#   #', '#   #', '#   #', '# # #', '#  # ', ' ## #'],
    'R': ['#### ', '#   #', '#   #', '#### ', '# #  ', '#  # ', '#   #'],
    'S': [' ####', '#    ', '#    ', ' ### ', '    #', '    #', '#### '],
    'T': ['#####', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  '],
    'U': ['#   #', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '],
    'V': ['#   #', '#   #', '#   #', '#   #', '#   #', ' # # ', '  #  '],
    '1': ['  #  ', ' ##  ', '  #  ', '  #  ', '  #  ', '  #  ', ' ### '],
    '2': [' ### ', '#   #', '    #', '   # ', '  #  ', ' #   ', '#####'],
    '!': ['  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '     ', '  #  '],
    ' ': ['     '] * 7,
}


def write(pic, x, y, words, colour, scale=1, rng=None, wobble=0):
    """Write words on a picture with the alphabet above, from its top left corner at x, y, each
    letter `scale` pixels to its dots. `wobble` moves each dot by up to that many pixels at
    random, for writing that looks scratched or scrawled by hand."""
    for i, letter in enumerate(words):
        rows = LETTERS[letter]
        for row, line in enumerate(rows):
            for col, dot in enumerate(line):
                if dot != '#':
                    continue
                px = x + (i * 6 + col) * scale
                py = y + row * scale
                if wobble and rng is not None:
                    px += int(rng.integers(-wobble, wobble + 1))
                    py += int(rng.integers(-wobble, wobble + 1))
                pic[max(py, 0):py + scale, max(px, 0):px + scale] = colour


# ---- up top: the observatory's offices ------------------------------------------------------------

def wallpaper(seed=1):
    """Old wallpaper: faded stripes with a small pattern between them, yellowed and stained by
    damp coming down from the ceiling, above a dark wooden rail along the bottom."""
    pic = picture((150, 132, 98))
    # stripes, 16 pixels apart (so 4 to a square, and they join up across squares)
    stripe = (X % 16 < 3)
    pic[stripe] *= 0.78
    # a small diamond between the stripes, every 16 pixels down
    diamond = (np.abs((X % 16) - 9.5) + np.abs((Y % 16) - 8) < 3)
    pic[diamond] *= 0.85
    pic = shade(pic, 0.9 + fractal(seed) * 0.2 + grain(seed, 0.04))
    # damp: brown stains running down from the top, uneven along the wall
    damp = np.clip((fractal(seed + 7, octaves=(4, 8)) * 1.6 - 0.55) - Y / 90, 0, 1)
    pic = blend(pic, (88, 70, 45), damp * 0.9)
    # patches where it has peeled off, showing grey plaster
    peel = fractal(seed + 3, octaves=(8, 16, 32)) > 0.71
    pic[peel] = (np.array([118, 112, 104]) * (0.85 + 0.15 * fractal(seed + 4)[peel, None]))
    # the rail along the bottom: dark varnished wood, with a lit top edge
    rail = Y >= 52
    wood = picture((70, 44, 26)) * (0.8 + 0.3 * noise(seed + 9, 16)[..., None])
    pic[rail] = wood[rail]
    pic[52, :] *= 1.6
    pic[53, :] *= 1.2
    return pic


def tally(seed=2):
    """The wallpaper with tally marks scratched all over it in groups of four (not five): someone
    counting the knocks, night after night."""
    pic = wallpaper(seed)
    rng = np.random.default_rng(seed)
    ink = np.array([45, 30, 22])
    for gy in range(4, 46, 10):
        for gx in range(3, 60, 12):
            if rng.random() < 0.12:
                continue   # a gap here and there
            slant = rng.integers(-1, 2)
            for mark in range(4):
                x = gx + mark * 2
                for y in range(gy, gy + 7):
                    pic[y, min(x + (y - gy) * slant // 6, 63)] = ink
    return pic


def window(seed=3):
    """A window in the wall: a painted wooden frame of four panes, and outside them the night,
    almost black, with rain running down the glass."""
    pic = wallpaper(seed)
    frame = (190, 182, 160)
    pic[6:50, 8:56] = frame
    # the night outside: very dark blue, a little lighter low down (the sky near the hills)
    glass = (np.abs(X - 31.5) < 21) & (Y >= 10) & (Y < 46) & (np.abs(X - 31.5) > 1.5) & (np.abs(Y - 28) > 1.5)
    night = picture((10, 14, 26)) + (Y[..., None] - 10) / 36 * np.array([6, 8, 12])
    # rain: thin streaks running down, brighter where they catch some light
    rng = np.random.default_rng(seed)
    streaks = np.zeros((SIZE, SIZE))
    for _ in range(26):
        x, y, length = rng.integers(10, 54), rng.integers(8, 44), rng.integers(4, 14)
        streaks[y:y + length, x] = rng.random() * 0.8 + 0.2
    night = night + streaks[..., None] * np.array([40, 48, 60])
    pic[glass] = night[glass]
    bevel_box(pic, 8, 6, 55, 49, 1.15, 0.7, 2)
    # the sill
    pic[50:53, 4:60] = (150, 140, 118)
    pic[50, 4:60] *= 1.3
    return pic


def panel(seed=4):
    """Dark wood panelling, in boards 16 pixels wide with grooves between, for the offices."""
    rng = np.random.default_rng(seed)
    pic = picture((92, 58, 34))
    # the grain: stretched noise (fine across, long down) with lines in it
    lines = noise(seed, 32)[:, :] * 0.6 + noise(seed + 1, 8) * 0.4
    streak = np.sin(X * 1.7 + lines * 9) * 0.5 + 0.5
    pic = shade(pic, 0.75 + streak * 0.3 + grain(seed, 0.03))
    for board in range(4):
        x0 = board * 16
        pic[:, x0:x0 + 16] *= 0.9 + rng.random() * 0.2
        pic[:, x0] *= 0.45            # the groove
        pic[:, x0 + 1] *= 1.2
    # a skirting board along the bottom
    pic[56:, :] *= 0.6
    pic[56, :] *= 1.8
    return pic


def carpet(seed=5):
    """The offices' carpet, worn: a small dark red and brown pattern, faded in a track down the
    middle where everyone walked, with old stains."""
    pic = picture((92, 40, 34))
    check = ((X // 8 + Y // 8) % 2 == 0)
    pic[check] *= 0.8
    dots = ((X % 8 - 3.5) ** 2 + (Y % 8 - 3.5) ** 2 < 3)
    pic[dots] = (120, 92, 52)
    pic = shade(pic, 0.8 + fractal(seed) * 0.35 + grain(seed, 0.08))
    wear = noise(seed + 2, 4) * 0.5
    pic = blend(pic, (110, 84, 72), wear)
    stains = np.clip(fractal(seed + 6, octaves=(8, 16)) * 3 - 2.0, 0, 1)
    pic = blend(pic, (40, 26, 20), stains)
    return pic


def ceiling_tiles(seed=6):
    """Ceiling tiles in a metal grid, 32 pixels square, pitted, with brown water stains."""
    pic = picture((168, 162, 146))
    pits = np.random.default_rng(seed).random((SIZE, SIZE)) < 0.08
    pic[pits] *= 0.75
    pic = shade(pic, 0.85 + fractal(seed) * 0.2)
    stain = np.clip(fractal(seed + 3, octaves=(4, 8, 16)) * 2.4 - 1.25, 0, 1)
    pic = blend(pic, (110, 84, 48), stain)
    grid = (X % 32 < 2) | (Y % 32 < 2)
    pic[grid] = (96, 96, 92)
    return pic


def door(seed=7):
    """A wooden door: four sunken panels, a brass handle, the paint worn round it."""
    pic = picture((104, 74, 46))
    pic = shade(pic, 0.85 + noise(seed, 32) * 0.25 + grain(seed, 0.03))
    pic[:, 0:4] = (60, 40, 26)        # the frame either side
    pic[:, 60:] = (60, 40, 26)
    for x0, x1 in ((10, 28), (35, 53)):
        for y0, y1 in ((6, 26), (32, 58)):
            bevel_box(pic, x0, y0, x1, y1, 0.6, 1.3, 2)
    pic[31:35, 52:57] = (178, 146, 64)    # the handle
    pic[31, 52:57] *= 1.3
    return pic


def entrance(seed=8):
    """The main doors: two glass doors in a metal frame, the night outside, and a chain looped
    through their handles from the inside."""
    pic = picture((70, 72, 74))
    pic = shade(pic, 0.9 + grain(seed, 0.05))
    glass = (np.abs(X - 31.5) > 3) & (X > 5) & (X < 58) & (Y > 5) & (Y < 54)
    night = picture((8, 11, 20)) + noise(seed, 8)[..., None] * np.array([6, 8, 14])
    pic[glass] = night[glass]
    # the chain: a sagging row of links between the handles
    for x in range(22, 42):
        y = int(34 + 3 * np.sin((x - 22) / 19 * np.pi))
        pic[y:y + 2, x] = (150, 150, 146) if x % 3 else (90, 90, 88)
    pic[28:40, 26:29] = (180, 180, 176)
    pic[28:40, 35:38] = (180, 180, 176)
    return pic


# ---- below: the lower level ------------------------------------------------------------------------

def concrete(seed=10, colour=(120, 118, 112)):
    """Painted concrete blocks, 32 x 16 pixels, every other row moved along half a block, with
    dark streaks where water has run down them."""
    pic = picture(colour)
    rng = np.random.default_rng(seed)
    for row in range(4):
        y0 = row * 16
        offset = 16 if row % 2 else 0
        for block in range(3):
            x0 = (block * 32 + offset) % SIZE
            cols = [(x0 + i) % SIZE for i in range(32)]
            pic[y0:y0 + 16, cols] *= 0.92 + rng.random() * 0.14
            pic[y0, cols] *= 0.55          # the mortar line along the top
            pic[y0 + 1, cols] *= 1.12
            pic[y0:y0 + 16, cols[0]] *= 0.6
    pic = shade(pic, 0.85 + fractal(seed) * 0.25 + grain(seed, 0.05))
    runs = noise(seed + 5, 16)[0:1, :].repeat(SIZE, axis=0)
    streak = np.clip(runs * 2.5 - 1.4, 0, 1) * (0.4 + Y / 80)
    pic = blend(pic, (52, 50, 44), streak)
    return pic


def pipes(seed=11):
    """Concrete with two pipes along it, held by brackets: a fat one with a valve wheel and a thin
    one, rusty, with rust run down the wall below them."""
    pic = concrete(seed, (112, 112, 108))
    for top, thick, colour in ((12, 10, (96, 84, 72)), (30, 5, (120, 104, 82))):
        for y in range(top, top + thick):
            # round: lit along its top, darker underneath
            curve = np.sin((y - top + 0.5) / thick * np.pi)
            light = 0.55 + 0.6 * curve * (1.2 - (y - top) / thick)
            pic[y, :] = np.array(colour) * light
        rust = noise(seed + top, 16)[top:top + thick, :] > 0.6
        pic[top:top + thick][rust] = (120, 62, 30)
        for x in (6, 38):     # brackets
            pic[top - 2:top + thick + 2, x:x + 3] = (60, 60, 60)
    # rust stains running down from the pipes
    run = np.clip(noise(seed + 9, 16)[0:1, :].repeat(SIZE, 0) * 2 - 1.1, 0, 1) * (Y > 35)
    pic = blend(pic, (110, 58, 30), run * 0.8)
    # the valve wheel on the fat pipe
    ring = np.abs(np.hypot(X - 48, Y - 17) - 6) < 1.3
    pic[ring] = (150, 30, 24)
    pic[17, 42:55] = (150, 30, 24)
    pic[11:24, 48] = (150, 30, 24)
    return pic


def tiles(seed=12):
    """Small white tiles, 16 pixels square, the grout gone dark, some cracked and one missing."""
    pic = picture((206, 208, 202))
    rng = np.random.default_rng(seed)
    for ty in range(4):
        for tx in range(4):
            pic[ty * 16:ty * 16 + 16, tx * 16:tx * 16 + 16] *= 0.9 + rng.random() * 0.12
    pic = shade(pic, 0.92 + fractal(seed) * 0.12)
    grout = (X % 16 < 1.5) | (Y % 16 < 1.5)
    pic[grout] = (70, 66, 58)
    # one tile gone, the grey adhesive behind it showing
    pic[33:47, 17:31] = (96, 94, 88) * (0.8 + 0.3 * noise(seed + 2, 16)[33:47, 17:31, None])
    # cracks: a few jagged lines
    for _ in range(3):
        x, y = rng.integers(0, 64), rng.integers(0, 64)
        for _ in range(14):
            pic[y % 64, x % 64] = (90, 88, 84)
            x += rng.integers(-1, 2)
            y += 1
    dirt = np.clip(fractal(seed + 4) * 2 - 0.9, 0, 1) * (Y / 64)
    return blend(pic, (96, 88, 64), dirt * 0.7)


def scrawl(seed=13):
    """Concrete with QUIET scratched into it in big, shaky letters."""
    pic = concrete(seed)
    rng = np.random.default_rng(seed)
    # each letter a pixel or so up or down from the last, as if written in a hurry
    for i, letter in enumerate('QUIET'):
        write(pic, 3 + i * 12, 18 + int(rng.integers(-2, 3)), letter, (36, 26, 22), scale=2)
    return pic


def shelves(seed=14):
    """The tape archive's metal shelves: three shelves of tape boxes and reels, some fallen."""
    pic = picture((24, 24, 26))
    rng = np.random.default_rng(seed)
    for shelf_y in (19, 40, 61):
        pic[shelf_y:shelf_y + 3, :] = (110, 112, 116)
        pic[shelf_y, :] *= 1.3
        x = 1
        while x < 62:
            kind = rng.random()
            if kind < 0.6:           # a tape box standing up
                w, h = rng.integers(4, 7), rng.integers(12, 17)
                colour = np.array([(150, 140, 120), (90, 60, 40), (60, 70, 90), (160, 150, 130)][rng.integers(0, 4)])
                pic[shelf_y - h:shelf_y, x:x + w] = colour * (0.8 + rng.random() * 0.3)
                pic[shelf_y - h + 3:shelf_y - h + 5, x + 1:x + w - 1] = (220, 216, 200)   # the label
            elif kind < 0.8:         # a reel, side on
                w = 3
                pic[shelf_y - 14:shelf_y, x:x + w] = (70, 64, 60)
            else:                    # a gap
                w = rng.integers(3, 8)
            x += w + 1
    pic[:, 0:2] = (90, 92, 96)       # the uprights
    pic[:, 62:] = (90, 92, 96)
    return shade(pic, 0.9 + grain(seed, 0.05))


def steel_door(seed=15):
    """A steel door, riveted, with a kick plate and a lever handle, scratched round the edges."""
    pic = picture((92, 98, 100))
    pic = shade(pic, 0.88 + fractal(seed) * 0.2 + grain(seed, 0.04))
    pic[:, 0:5] = (50, 52, 54)
    pic[:, 59:] = (50, 52, 54)
    bevel_box(pic, 5, 2, 58, 63, 1.2, 0.6, 1)
    for y in (6, 58):
        for x in range(9, 56, 8):
            pic[y:y + 2, x:x + 2] = (150, 154, 156)
    pic[48:62, 6:58] *= 0.75           # the kick plate
    pic[48, 6:58] *= 1.5
    pic[30:33, 46:56] = (170, 170, 166)   # the handle
    scratches = np.clip(noise(seed + 3, 16) * 3 - 2.4, 0, 1)
    return blend(pic, (140, 142, 140), scratches * 0.6)


def lift(seed=16):
    """The lift's doors: two brushed steel halves meeting in the middle, a floor dial above
    them, and rust creeping up from the bottom."""
    pic = picture((128, 132, 134))
    streaks = noise(seed, 32)[:1, :].repeat(SIZE, 0) * 0.15
    pic = shade(pic, 0.85 + streaks + grain(seed, 0.03))
    pic[:, 31:33] = (30, 30, 32)
    pic[:, 0:3] = (60, 60, 60)
    pic[:, 61:] = (60, 60, 60)
    pic[0:10, :] = (70, 64, 52)
    ring = np.abs(np.hypot(X - 32, Y - 5) - 3.5) < 1
    pic[ring] = (190, 170, 90)
    pic[3:6, 32:35] = (200, 40, 30)        # the needle, pointing down
    rust = np.clip(fractal(seed + 4) * 2 - 1.0 + (Y - 64) / 50, 0, 1)
    return blend(pic, (104, 54, 28), rust)


def stairs(seed=17):
    """A steel door with a sign above its window: STAIRS B2."""
    pic = steel_door(seed)
    pic[8:17, 14:50] = (200, 190, 60)
    write(pic, 15, 9, 'STAIRS', (20, 20, 20))
    pic[20:30, 24:40] = (10, 10, 12)     # a small window, black behind it
    bevel_box(pic, 23, 19, 40, 30, 0.6, 1.3, 1)
    return pic


def generator(seed=18):
    """The emergency generator's control face: green steel, two gauges, a row of switches and
    the empty fuse holder."""
    pic = picture((70, 92, 74))
    pic = shade(pic, 0.85 + fractal(seed) * 0.2 + grain(seed, 0.04))
    bevel_box(pic, 2, 2, 61, 61, 1.25, 0.6, 2)
    for cx in (18, 44):
        dial = np.hypot(X - cx, Y - 18) < 9
        pic[dial] = (220, 214, 190)
        pic[np.abs(np.hypot(X - cx, Y - 18) - 9) < 1] = (30, 30, 30)
        pic[17:19, cx - 6:cx] = (160, 20, 20)
    for x in range(10, 54, 8):
        pic[34:42, x:x + 4] = (30, 30, 30)
        pic[34:37, x + 1:x + 3] = (190, 190, 180)
    pic[46:58, 24:40] = (24, 22, 20)      # the fuse holder
    bevel_box(pic, 24, 46, 39, 57, 0.6, 1.3, 1)
    return pic


def switchboard(seed=19):
    """The lower level's power board: grey panels, a big lever (pulled down: off), a red lamp."""
    pic = picture((110, 112, 108))
    pic = shade(pic, 0.85 + fractal(seed) * 0.2 + grain(seed, 0.04))
    bevel_box(pic, 3, 3, 60, 60, 1.2, 0.6, 2)
    pic[14:50, 26:38] = (40, 40, 40)
    pic[40:50, 22:42] = (170, 30, 24)     # the lever's handle, down
    pic[40, 22:42] *= 1.4
    lamp = np.hypot(X - 50, Y - 12) < 4
    pic[lamp] = (200, 30, 20)
    for y in range(10, 56, 6):
        pic[y, 8:18] = (60, 60, 60)
    return pic


def concrete_floor(seed=20):
    """The lower level's floor: bare concrete with saw cuts every 32 pixels, oil stains, grit."""
    pic = picture((98, 96, 90))
    pic = shade(pic, 0.8 + fractal(seed) * 0.35 + grain(seed, 0.1))
    cut = (X % 32 < 1) | (Y % 32 < 1)
    pic[cut] *= 0.55
    stain = np.clip(fractal(seed + 2, octaves=(4, 8, 16)) * 2.6 - 1.5, 0, 1)
    return blend(pic, (40, 38, 34), stain)


def concrete_ceiling(seed=21):
    """The lower level's ceiling: concrete slabs with a cable tray running across."""
    pic = picture((84, 84, 82))
    pic = shade(pic, 0.8 + fractal(seed) * 0.3 + grain(seed, 0.06))
    pic[(Y % 64) < 1] *= 0.5
    pic[26:38, :] = (50, 50, 52)
    for x in range(0, 64, 4):
        pic[26:38, x] = (70, 70, 72)
    pic[29:31, :] = (130, 40, 30)
    pic[33:35, :] = (40, 60, 120)
    return pic


# ---- at the bottom: the borehole --------------------------------------------------------------------

def rock(seed=30):
    """Bare rock: layers (strata) of slightly different stone, cracked, and wet in places."""
    strata = noise(seed, 4)[:, :1].repeat(SIZE, 1) * 0.0 + np.sin(Y / 64 * np.pi * 6 + noise(seed + 1, 4) * 4) * 0.5 + 0.5
    pic = picture((82, 70, 60))
    pic = shade(pic, 0.6 + fractal(seed) * 0.45 + strata * 0.15 + grain(seed, 0.07))
    # cracks: dark where a lower noise crosses its middle
    crack = np.abs(fractal(seed + 5, octaves=(4, 8, 16)) - 0.5) < 0.018
    pic[crack] *= 0.35
    wet = np.clip(noise(seed + 8, 8) * 2.2 - 1.3, 0, 1)
    return blend(pic, (40, 46, 48), wet * 0.6)


def shaft(seed=31):
    """The borehole's casing: riveted steel plates, yellow and black hazard stripes round its foot."""
    pic = picture((88, 90, 86))
    pic = shade(pic, 0.8 + fractal(seed) * 0.3 + grain(seed, 0.05))
    pic[(Y % 32) < 1.5] *= 0.5
    for y in (4, 28, 36):
        for x in range(4, 64, 10):
            pic[y:y + 2, x:x + 2] = (140, 140, 134)
    hazard = (Y >= 48) & (Y < 60)
    stripe = ((X + Y) // 8) % 2 == 0
    pic[hazard & stripe] = (200, 170, 30)
    pic[hazard & ~stripe] = (20, 20, 18)
    rust = np.clip(fractal(seed + 2) * 2.2 - 1.1, 0, 1)
    return blend(pic, (110, 56, 26), rust)


def console(seed=32):
    """The charge console in the casing: CHARGE above a key slot, a red button and a slot for a
    tape, on a grey panel."""
    pic = shaft(seed)
    pic[6:46, 6:58] = (120, 122, 118)
    bevel_box(pic, 6, 6, 57, 45, 1.25, 0.6, 2)
    pic[9:18, 8:56] = (20, 20, 20)
    write(pic, 10, 10, 'CHARGE', (220, 60, 40), 1)
    pic[24:36, 10:30] = (36, 34, 30)       # the tape slot
    pic[29:31, 12:28] = (10, 10, 10)
    button = np.hypot(X - 44, Y - 28) < 6
    pic[button] = (190, 30, 24)
    pic[np.hypot(X - 41, Y - 25) < 2] = (240, 120, 100)
    pic[38:42, 40:48] = (190, 160, 60)     # the key slot
    return pic


def ladder(seed=33):
    """Rock with a steel ladder fixed to it, going up into the dark."""
    pic = rock(seed)
    pic[:, 18:22] = (110, 112, 110)
    pic[:, 42:46] = (110, 112, 110)
    pic[:, 18] *= 1.4
    pic[:, 42] *= 1.4
    for y in range(4, 64, 10):
        pic[y:y + 3, 18:46] = (130, 132, 128)
        pic[y, 18:46] *= 1.3
    return pic


def rock_floor(seed=34):
    """The cavern floor: rock and grit, with a steel grating walkway's edge... just rock."""
    pic = picture((70, 62, 54))
    pic = shade(pic, 0.65 + fractal(seed) * 0.45 + grain(seed, 0.12))
    puddle = np.clip(noise(seed + 3, 4) * 2.4 - 1.5, 0, 1)
    return blend(pic, (26, 30, 32), puddle * 0.8)


def rock_ceiling(seed=35):
    """The cavern roof: darker rock, rougher."""
    return rock(seed) * 0.75


PICTURES = {
    'wallpap': wallpaper, 'tally': tally, 'window': window, 'panel': panel, 'carpet': carpet,
    'ceiltile': ceiling_tiles, 'door': door, 'entrance': entrance,
    'concrete': concrete, 'pipes': pipes, 'tiles': tiles, 'scrawl': scrawl, 'shelves': shelves,
    'steeldr': steel_door, 'lift': lift, 'stairs': stairs, 'genny': generator, 'switch': switchboard,
    'cfloor': concrete_floor, 'cceil': concrete_ceiling,
    'rock': rock, 'shaft': shaft, 'console': console, 'ladder': ladder, 'rfloor': rock_floor,
    'rceil': rock_ceiling,
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, make in PICTURES.items():
        save(name, make())
    print(f'{len(PICTURES)} pictures written to {OUT}')


if __name__ == '__main__':
    main()
