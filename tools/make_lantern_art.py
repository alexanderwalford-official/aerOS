#!/usr/bin/env python3
"""
make_lantern_art.py - draw Lantern's pixel art: the land, the people, the monsters and the rest

    python3 tools/make_lantern_art.py            (needs Pillow, which WSL has)
    python3 tools/make_lantern_art.py preview DIR   also writes big PNGs of every sheet into DIR

writes them to packages/lantern/, where pkg install puts them in /apps/lantern/ next to the game
(packages/lantern.aer). Everything is drawn here, a pixel at a time: nothing is copied from
anywhere. The land is worked out from a few colours and seeded noise (grass blades, ripples,
bricks), and the people and monsters are drawn as little grids of letters, each letter a colour
of the palette below.

THE SHEETS

The game draws with imgcol(), which shows a range of a picture's columns stretched over a box.
So every sheet is one row of pictures side by side, all as tall as the sheet: picture n is
columns n * size to (n + 1) * size. The game picks one by its number, so the lists TILES and
SPRITES below must stay in the same order as the numbers at the top of lantern.aer.

    tiles0.bmp ... tiles3.bmp     the land, 16 x 16 each, in four lights: day, golden (late
                                  afternoon, and anything a lantern or a fire lights), dusk,
                                  and night (blue). The game picks one by the time of day
    spr0.bmp ... spr3.bmp         people, monsters and things, 16 x 16, in the same four lights
    boss.bmp                      the four villains, 32 x 32, two pictures each
    hud.bmp                       hearts, coins and the other little signs on the screen, 8 x 8
    logo.bmp                      the title

They are 32 bit BMPs (the quickest kind for imgcol), the 4th byte of each pixel saying how solid
it is: 255 solid, 0 see-through. Running this again makes exactly the same files.
"""

import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'lantern')

# ---- the palette ------------------------------------------------------------------------------
#
# Every colour in the game's pictures, by the letter the grids below use for it. '.' is
# see-through.

PALETTE = {
    'K': (26, 20, 35),      # outline, almost black with a little purple
    'k': (60, 46, 72),      # deep shadow
    'W': (244, 240, 230),   # white
    'w': (176, 176, 192),   # light grey
    'g': (122, 122, 142),   # grey
    'G': (74, 74, 94),      # dark grey
    'S': (240, 196, 160),   # skin
    's': (197, 138, 94),    # skin in shadow
    'H': (107, 62, 38),     # brown hair
    'h': (59, 36, 24),      # dark brown
    'Y': (242, 201, 76),    # gold
    'y': (255, 244, 163),   # pale yellow, flame
    'O': (245, 154, 106),   # orange
    'R': (168, 50, 62),     # red
    'r': (224, 85, 74),     # bright red
    'D': (92, 26, 43),      # dark red
    'B': (62, 92, 154),     # blue
    'b': (95, 160, 216),    # light blue
    'C': (158, 230, 240),   # cyan, magic
    'N': (43, 45, 82),      # navy
    'E': (79, 154, 63),     # green
    'e': (143, 207, 74),    # light green
    'F': (46, 107, 58),     # dark green
    'f': (31, 61, 43),      # deep green
    'T': (210, 160, 102),   # tan
    't': (156, 106, 60),    # brown
    'P': (122, 75, 176),    # purple
    'p': (185, 139, 224),   # light purple
    'Q': (75, 42, 107),     # deep purple
    'L': (212, 239, 122),   # lime
    'M': (232, 207, 143),   # sand
    'm': (244, 236, 194),   # pale sand
}

# The four lights every land and sprite sheet comes in: how much of each of red, green and
# blue is kept, and a little blue added at night
LIGHTS = [
    ((1.00, 1.00, 1.00), (0, 0, 0)),     # 0 day
    ((1.06, 0.94, 0.76), (6, 2, 0)),     # 1 golden: late afternoon, and lamplight
    ((0.92, 0.74, 0.86), (4, 0, 10)),    # 2 dusk
    ((0.42, 0.58, 0.98), (0, 4, 22)),    # 3 night
]


def tinted(colour, light):
    (mr, mg, mb), (ar, ag, ab) = LIGHTS[light]
    r, g, b = colour
    return (min(255, int(r * mr + ar)), min(255, int(g * mg + ag)), min(255, int(b * mb + ab)))


# ---- a picture being drawn --------------------------------------------------------------------

class Pic:
    """A w x h picture: a colour (r, g, b) or None (see-through) for each pixel."""

    def __init__(self, w=16, h=16, fill=None):
        self.w, self.h = w, h
        self.px = [[fill for _ in range(w)] for _ in range(h)]

    def set(self, x, y, colour):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = PALETTE[colour] if isinstance(colour, str) else colour

    def get(self, x, y):
        return self.px[y % self.h][x % self.w]

    def copy(self):
        c = Pic(self.w, self.h)
        c.px = [row[:] for row in self.px]
        return c

    def grid(self, rows, colours=None, dx=0, dy=0):
        """Draw a grid of letters over the picture. Each letter is a palette colour, or (with
        `colours`) is first looked up there, so one grid can be drawn in different clothes.
        '.' leaves the pixel as it is."""
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == '.':
                    continue
                if colours and ch in colours:
                    ch = colours[ch]
                if ch == '.':
                    continue
                self.set(x + dx, y + dy, ch)
        return self

    def mirrored(self):
        c = Pic(self.w, self.h)
        c.px = [row[::-1] for row in self.px]
        return c

    def scaled(self, n):
        c = Pic(self.w * n, self.h * n)
        for y in range(c.h):
            for x in range(c.w):
                c.px[y][x] = self.px[y // n][x // n]
        return c


def check(name, rows, width=16):
    """A grid's rows must all be the same width: a mistake shows up here, not as a squint."""
    for i, row in enumerate(rows):
        if len(row) != width:
            raise SystemExit(f'{name}: row {i} is {len(row)} wide, not {width}: "{row}"')
    return rows


def write_bmp(path, pics, light=0):
    """Write pictures side by side as one 32 bit BMP (bottom row first, blue green red alpha)."""
    w = sum(p.w for p in pics)
    h = pics[0].h
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        for p in pics:
            for x in range(p.w):
                c = p.px[y][x]
                if c is None:
                    rows += bytes((0, 0, 0, 0))
                else:
                    r, g, b = tinted(c, light)
                    rows += bytes((b, g, r, 255))
    header = struct.pack('<2sIHHI', b'BM', 54 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 32, 0, len(rows), 2835, 2835, 0, 0)
    with open(path, 'wb') as f:
        f.write(header + info + rows)


# ---- the land ---------------------------------------------------------------------------------
#
# Each tile is drawn by a function, with its own seeded random numbers so it comes out the
# same every time. Most start from a ground (grass, stone...) and draw something on it.

def rng(name):
    return random.Random(name)


def speckle(p, r, colour, share, avoid=None):
    """Scatter single pixels of a colour over a share of the picture."""
    for y in range(p.h):
        for x in range(p.w):
            if r.random() < share and (avoid is None or p.px[y][x] != PALETTE[avoid]):
                p.set(x, y, colour)


def grass(seed='grass', tufts=10):
    r = rng(seed)
    p = Pic(fill=PALETTE['E'])
    speckle(p, r, 'F', 0.10)
    speckle(p, r, 'e', 0.04)
    # little blades: a light tip over a dark root
    for _ in range(tufts):
        x, y = r.randrange(16), r.randrange(1, 16)
        p.set(x, y, 'F')
        p.set(x, y - 1, 'e')
    return p


def sand():
    r = rng('sand')
    p = Pic(fill=PALETTE['M'])
    speckle(p, r, 'm', 0.08)
    speckle(p, r, 'T', 0.06)
    return p


def road():
    r = rng('road')
    p = Pic(fill=PALETTE['T'])
    speckle(p, r, 't', 0.14)
    speckle(p, r, 'M', 0.05)
    for _ in range(4):   # pebbles
        x, y = r.randrange(15), r.randrange(15)
        p.set(x, y, 'w')
        p.set(x + 1, y + 1, 'g')
    return p


def water(frame, shore=False):
    """Deep water with ripples that drift sideways from frame to frame (3 frames). A shore tile
    has the bank and a line of foam along its top."""
    r = rng('water')
    p = Pic(fill=PALETTE['B'])
    speckle(p, r, 'N', 0.05)
    # ripples: short light dashes on every 4th row, moving right a bit each frame
    for y in range(1, 16, 4):
        start = (y * 5 + frame * 3) % 16
        for i in range(4):
            p.set((start + i) % 16, y, 'b')
        p.set((start + 9) % 16, y + 2, 'b')
    if frame == 1:
        p.set(4, 6, 'C')
    if frame == 2:
        p.set(11, 13, 'C')
    if shore:
        for x in range(16):
            # the bank, then a wavy line of foam that laps up and down with the frames
            p.set(x, 0, 'M')
            p.set(x, 1, 'T' if (x + frame) % 5 else 'M')
            wave = 2 + ((x // 3 + frame) % 2)
            p.set(x, wave, 'W')
            p.set(x, wave + 1, 'C' if x % 2 else 'b')
            for y in range(2, wave):
                p.set(x, y, 'b')
    return p


def bridge(vertical):
    p = Pic(fill=PALETTE['t'])
    for i in range(16):
        for j in range(16):
            x, y = (i, j) if not vertical else (j, i)
            # planks across the way, a dark line between each; rails at the sides
            if i % 4 == 3:
                p.set(x, y, 'H')
            elif (i * 7 + j) % 11 == 0:
                p.set(x, y, 'T')
    for k in range(16):
        for edge in (0, 15):
            x, y = (k, edge) if not vertical else (edge, k)
            p.set(x, y, 'h')
        for edge in (1, 14):
            x, y = (k, edge) if not vertical else (edge, k)
            if k % 4 == 0:
                p.set(x, y, 'h')
    return p


TREE = check('tree', [
    "....KKKKKKK.....",
    "..KKeeeEEEEKK...",
    ".KeeLeeEEEEEEK..",
    ".KeLeeEEEEEFEEK.",
    "KeeeEEEEFEEEEEK.",
    "KeeEEEFEEEEEFFK.",
    "KEEEEEEEEEFEEFFK",
    "KEEFEEEEEEEEFFFK",
    ".KEEEEFEEEEFFFK.",
    ".KFEEEEEEFFFFFK.",
    "..KFFEEFFFFFfK..",
    "...KKFFFFfffK...",
    ".....KKtHKKK....",
    "......KtHK......",
    ".....KttHHK.....",
    "......KKKK......",
])

PINE = check('pine', [
    ".......KK.......",
    "......KeEK......",
    ".....KeEEFK.....",
    "....KeEEEFFK....",
    "...KKeEEFFKKK...",
    "....KeEEEFFK....",
    "...KeEEEEFFFK...",
    "..KeeEEEEFFFFK..",
    "..KKKeEEFFFKKK..",
    "...KeEEEEFFFK...",
    "..KeEEEEEFFFFK..",
    ".KeEEEEEEFFFFfK.",
    ".KKKKKKtHKKKKKK.",
    "......KtHK......",
    "......KtHK......",
    ".......KK.......",
])

BUSH = check('bush', [
    "................",
    "................",
    "................",
    "................",
    "......KKKK......",
    "....KKeeEEKK....",
    "...KeeLeEEEFK...",
    "..KeLeEEEEEFFK..",
    "..KeeEEErEEFFK..",
    ".KeEEEEEEEEFFFK.",
    ".KEEEEFEEEEFFFK.",
    ".KFEEEEEEErFFfK.",
    "..KFFEEFFFFFfK..",
    "...KKFFFffffK...",
    ".....KKKKKKK....",
    "................",
])

ROCK = check('rock', [
    "................",
    "................",
    "................",
    "................",
    "................",
    ".....KKKKKK.....",
    "...KKwwWwggKK...",
    "..KwwWwwggggGK..",
    ".KwWwwgggggGGK..",
    ".KwwwgggKggGGGK.",
    ".KgwgggggKgGGGK.",
    ".KggggggggGGGkK.",
    "..KGgggggGGGkK..",
    "...KKGGGGGkkK...",
    ".....KKKKKKK....",
    "................",
])


def on(ground, rows, colours=None):
    return ground.copy().grid(rows, colours)


def mountain():
    """The top of a mountain: rough grey rock with cracks."""
    r = rng('mountain')
    p = Pic(fill=PALETTE['g'])
    speckle(p, r, 'G', 0.18)
    speckle(p, r, 'w', 0.10)
    for _ in range(3):   # cracks
        x, y = r.randrange(16), r.randrange(16)
        for i in range(4):
            p.set(x + i // 2, y + i, 'k')
    for _ in range(2):   # bright edges catching the sun
        x, y = r.randrange(14), r.randrange(16)
        p.set(x, y, 'W')
        p.set(x + 1, y, 'w')
    return p


def cliff():
    """The face of a mountain seen from the front: dark rock in vertical ridges, a bright lip on
    top and a shadow at the foot."""
    r = rng('cliff')
    p = Pic(fill=PALETTE['G'])
    for x in range(16):
        shade = 'g' if x % 5 in (1, 2) else ('k' if x % 5 == 4 else 'G')
        for y in range(16):
            if r.random() < 0.85:
                p.set(x, y, shade)
    for x in range(16):
        p.set(x, 0, 'w')
        p.set(x, 1, 'g')
        p.set(x, 15, 'K')
        p.set(x, 14, 'k')
    speckle(p, r, 'K', 0.03)
    return p


def swamp():
    r = rng('swamp')
    p = Pic(fill=(70, 96, 56))
    speckle(p, r, 'F', 0.2)
    speckle(p, r, (96, 110, 60), 0.08)
    # dark pools
    for cx, cy in ((4, 5), (11, 11)):
        for y in range(-2, 3):
            for x in range(-3, 4):
                if x * x + y * y * 2 < 9:
                    p.set(cx + x, cy + y, 'N' if (x + y) % 3 else (36, 60, 70))
        p.set(cx - 1, cy - 1, 'B')
    return p


def reeds():
    p = swamp()
    for x in (3, 6, 7, 10, 12):
        top = 3 + (x * 7) % 5
        for y in range(top, 15):
            p.set(x, y, 'F' if y > top + 2 else 'e')
        p.set(x, top - 1, 't')
        p.set(x, top - 2, 'H')
    return p


DEADTREE = check('deadtree', [
    "................",
    "...K.......K....",
    "...Kh..K..KhK...",
    "....Kh.Kh.Kh....",
    ".....KhKhKh.....",
    "..Kh..KhhK..KK..",
    "...KhhKhhKhhK...",
    ".....KKhhK......",
    "......KhhK......",
    "......KhhK......",
    "......KhHK......",
    "......KhHK......",
    ".....KhhHHK.....",
    "....KhK..KhK....",
    "................",
    "................",
])


def blight():
    """The villain's corruption on the land: ash-grey earth with veins of purple."""
    r = rng('blight')
    p = Pic(fill=(58, 46, 66))
    speckle(p, r, 'k', 0.2)
    speckle(p, r, 'G', 0.08)
    x, y = 2, 3
    for _ in range(14):    # a wandering vein
        p.set(x, y, 'Q')
        x = (x + r.choice((1, 1, 0))) % 16
        y = (y + r.choice((1, 0, 0))) % 16
    for _ in range(3):
        p.set(r.randrange(16), r.randrange(16), 'P')
    return p


def roof(right):
    """Half of a house's roof: rows of red shingles, the ridge along the top, a chimney on the
    right half."""
    p = Pic(fill=PALETTE['R'])
    for y in range(16):
        for x in range(16):
            if y % 4 == 3:
                p.set(x, y, 'D')
            elif (x + (y // 4) * 2) % 4 == 0:
                p.set(x, y, 'D')
            elif y % 4 == 0:
                p.set(x, y, 'r')
    for x in range(16):
        p.set(x, 0, 'K')
        p.set(x, 1, 'O')
        p.set(x, 15, 'K')
    edge = 15 if right else 0
    for y in range(16):
        p.set(edge, y, 'K')
    if right:
        p.grid(["", "", "", "........KKKK....", "........KggK....", "........KgGK....",
                "........KGGK....", "........KKKK...."])
    return p


def wall(kind):
    """The front of a house: plaster in a timber frame. kind is 'window', 'lit' (the window
    glowing at night), 'door', or 'burnt'."""
    p = Pic(fill=PALETTE['m'])
    r = rng('wall' + kind)
    speckle(p, r, 'M', 0.12)
    for y in range(16):
        p.set(0, y, 'K')
        p.set(15, y, 'K')
        p.set(1, y, 't')
        p.set(14, y, 't')
    for x in range(16):
        p.set(x, 0, 'k')
        p.set(x, 1, 't')
        p.set(x, 14, 'G')
        p.set(x, 15, 'K')
    if kind in ('window', 'lit', 'burnt'):
        glass = {'window': 'N', 'lit': 'Y', 'burnt': 'K'}[kind]
        for y in range(4, 11):
            for x in range(4, 12):
                edge = y in (4, 10) or x in (4, 11)
                p.set(x, y, 't' if edge else glass)
        for y in range(5, 10):
            p.set(7, y, 't')
            p.set(8, y, 't')
        if kind == 'lit':
            p.set(5, 5, 'y')
            p.set(6, 5, 'y')
            p.set(9, 6, 'y')
        if kind == 'window':
            p.set(5, 5, 'b')
            p.set(9, 5, 'b')
    if kind == 'door':
        for y in range(4, 15):
            for x in range(4, 12):
                edge = y == 4 or x in (4, 11)
                p.set(x, y, 'h' if edge else ('H' if x % 2 else 't'))
        p.set(10, 10, 'Y')
    if kind == 'burnt':
        speckle(p, r, 'K', 0.35)
        speckle(p, r, 'G', 0.2)
        speckle(p, r, 'O', 0.03)
    return p


def burnt_roof():
    r = rng('burntroof')
    p = Pic(fill=PALETTE['k'])
    speckle(p, r, 'K', 0.4)
    speckle(p, r, 'G', 0.15)
    speckle(p, r, 'O', 0.04)
    speckle(p, r, 'r', 0.02)
    for x in range(16):
        p.set(x, 15, 'K')
    return p


def plaza():
    """Cobblestones: rounded stones in staggered rows, mortar between."""
    p = Pic(fill=PALETTE['G'])
    r = rng('plaza')
    for row in range(4):
        shift = 2 if row % 2 else 0
        for col in range(-1, 4):
            x0 = col * 4 + shift
            for y in range(row * 4, row * 4 + 3):
                for x in range(x0, x0 + 3):
                    p.set(x % 16, y, 'g' if r.random() < 0.8 else 'w')
            p.set(x0 % 16, row * 4, 'w')
    return p


def well():
    p = plaza()
    p.grid(check('well', [
        "................",
        "................",
        "....KKKKKKKK....",
        "...KwwWwwwwgK...",
        "..KwgKKKKKKggK..",
        "..KwKNNNNNNKgK..",
        "..KwKNBBbBNKgK..",
        "..KgKNBbBBNKGK..",
        "..KgKNNNNNNKGK..",
        "..KggKKKKKKGGK..",
        "...KgggggGGGK...",
        "....KKKKKKKK....",
        "................",
        "................",
        "................",
        "................",
    ]))
    return p


FENCE = check('fence', [
    "................",
    "................",
    "................",
    "..KK......KK....",
    ".KTtK....KTtK...",
    "KKTtKKKKKKTtKKKK",
    "TTTtTTTTTTTtTTTT",
    "tttttttttttttttt",
    "KKTtKKKKKKTtKKKK",
    "TTTtTTTTTTTtTTTT",
    "tttttttttttttttt",
    "KKTtKKKKKKTtKKKK",
    "..KtK.....KtK...",
    "..KKK.....KKK...",
    "................",
    "................",
])


def ruin_floor():
    """Old flagstones, cracked, with moss in the gaps."""
    p = Pic(fill=PALETTE['g'])
    r = rng('ruin')
    for y in range(16):
        for x in range(16):
            if y % 8 == 0 or (x + (y // 8) * 4) % 8 == 0:
                p.set(x, y, 'G')
    speckle(p, r, 'w', 0.06)
    speckle(p, r, 'E', 0.07)
    speckle(p, r, 'F', 0.04)
    for i in range(5):
        p.set(3 + i, 10 + i // 2, 'k')
    return p


RUINWALL = check('ruinwall', [
    "................",
    "....KKKKKK......",
    "...KWwwwwwK.....",
    "...KwwwwggK.KK..",
    "...KwgwwggKKwgK.",
    "...KwgwwggKwwgK.",
    "...KKKKKKKKwggK.",
    "...KwwwwggKKKKK.",
    "...KwgwwggKwwgK.",
    "...KwgEwggKwggK.",
    "...KwgwwgEKwEgK.",
    "..KKKKKKKKKKKKKK",
    "..KwwwwwggggGGGK",
    "..KgggEggggEGGGK",
    "..KKKKKKKKKKKKKK",
    "................",
])


def cave():
    p = cliff()
    p.grid(check('cave', [
        "................",
        "................",
        "................",
        ".....KKKKKK.....",
        "....KkKKKKkK....",
        "...KkKKKKKKkK...",
        "...KkKKKKKKkK...",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
        "..KkKKKKKKKKkK..",
    ]))
    return p


def dungeon_floor(variant):
    p = Pic(fill=(64, 60, 76))
    r = rng('dfloor%d' % variant)
    for y in range(16):
        for x in range(16):
            if y % 8 == 7 or (x + (y // 8) * 5) % 8 == 7:
                p.set(x, y, 'k')
    speckle(p, r, 'G', 0.15)
    speckle(p, r, (84, 80, 96), 0.1)
    if variant:
        # a few old bones
        p.grid(["", "", "", "", "", "", "", "", "", ".......w........", "....wWWwW.......",
                ".....w..........", "..........WW....", "...........w...."])
    return p


def dungeon_wall_top():
    p = Pic(fill=(36, 30, 44))
    r = rng('dtop')
    speckle(p, r, 'K', 0.25)
    speckle(p, r, 'k', 0.1)
    return p


def bricks(base, mortar, light, seed):
    p = Pic(fill=PALETTE[base] if isinstance(base, str) else base)
    r = rng(seed)
    for y in range(16):
        for x in range(16):
            if y % 4 == 3 or (x + (4 if (y // 4) % 2 else 0)) % 8 == 7:
                p.set(x, y, mortar)
            elif y % 4 == 0 and r.random() < 0.6:
                p.set(x, y, light)
    for x in range(16):   # dark at the foot, where the wall meets the floor
        p.set(x, 15, 'K')
        p.set(x, 14, 'k')
    return p


def dungeon_wall():
    return bricks('G', 'k', 'g', 'dwall')


def torch(frame):
    p = dungeon_wall()
    flame = [
        ["......y.........", ".....yY.........", ".....YOy........", "....yYOY........",
         "....YOrO........", ".....OrY........"],
        [".......y........", "......Yy........", ".....yOY........", ".....YOYy.......",
         "....YOrOY.......", ".....YrO........"],
    ][frame]
    p.grid(flame, dx=2, dy=1)
    p.grid(["", "", "", "", "", "", "......KK........", ".....KtHK.......", "......tH........",
            "......KK........"], dx=1)
    return p


def stairs():
    p = Pic(fill=PALETTE['G'])
    for step in range(4):
        y0 = step * 4
        shade = ['W', 'w', 'g', 'G'][step]
        for y in range(y0, y0 + 3):
            for x in range(1, 15):
                p.set(x, y, shade)
        for x in range(1, 15):
            p.set(x, y0 + 3, 'k')
    for y in range(16):
        p.set(0, y, 'K')
        p.set(15, y, 'K')
    return p


SHRINE = check('shrine', [
    ".......KK.......",
    "......KCCK......",
    ".....KCWWCK.....",
    "......KCCK......",
    ".....KwwwgK.....",
    "....KwWwwggK....",
    "....KwwCCwgK....",
    "....KwCbbCgK....",
    "....KwwCCwgK....",
    "....KwwwwggK....",
    "....KwwwwggK....",
    "...KKwwwwggKK...",
    "..KwwwwwwgggGK..",
    "..KgggggggGGGK..",
    "...KKKKKKKKKK...",
    "................",
])

GRAVE = check('grave', [
    "................",
    "................",
    "................",
    ".....KKKKK......",
    "....KwwwwgK.....",
    "...KwwWwwggK....",
    "...KwwKKKggK....",
    "...KwwwKwggK....",
    "...KwwwKwggK....",
    "...KwwwwwggK....",
    "...KwwwwwggK....",
    "...KgggggGGK....",
    "..KtttttttttK...",
    "..KttHtttHttK...",
    "...KKKKKKKKK....",
    "................",
])


def campfire(frame):
    p = road()
    flames = [[
        "................", "................", "................", ".......y........",
        "......yY........", "......YO..y.....", ".....YOYyYY.....", ".....OrOYOY.....",
        "....YOrrOrOY....", "....OrrDrrOY....",
    ], [
        "................", "................", "........y.......", "........Yy......",
        ".....y.YO.......", ".....YYOY.......", "....yYOrOY......", ".....OrrOYy.....",
        "....YOrDrrOY....", "....ORrDDrrO....",
    ]][frame]
    p.grid(flames)
    p.grid(["", "", "", "", "", "", "", "", "", "", "...KHHKKKKHHK...", "..KtHHtttHHtHK..",
            "..KKtttHHtttKK..", "...KKKKKKKKKK..."])
    return p


def lair_floor():
    p = Pic(fill=(46, 32, 58))
    r = rng('lfloor')
    for y in range(16):
        for x in range(16):
            if y % 8 == 7 or (x + (y // 8) * 3) % 8 == 7:
                p.set(x, y, 'K')
    speckle(p, r, 'Q', 0.12)
    speckle(p, r, 'k', 0.12)
    p.set(4, 3, 'P')
    p.set(12, 11, 'P')
    return p


def lair_wall():
    p = bricks('Q', 'K', 'P', 'lwall')
    p.set(5, 5, 'p')
    p.set(13, 9, 'p')
    return p


def lair_wall_top():
    """The top of the lair's wall: battlements, dark stone teeth against the sky. Inside a cave
    it is the dark top of a wall, so the gaps are dark stone too."""
    p = Pic(fill=(30, 18, 40))
    speckle(p, rng('ltop'), 'K', 0.3)
    for x in range(16):
        merlon = x % 8 < 5
        for y in range(6, 16):
            if merlon or y >= 10:
                edge = y == 6 or x % 8 in (0, 4) or y == 10
                p.set(x, y, 'K' if edge else ('P' if y in (7, 11) else 'Q'))
    return p


def gate(open_):
    p = lair_wall()
    if open_:
        p.grid(["..KKKKKKKKKKKK..", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.",
                ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.",
                ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.",
                ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK.", ".KkKKKKKKKKKKkK."])
        p.set(6, 14, 'w')
        p.set(9, 13, 'w')
    else:
        p.grid(check('gate', [
            "..KKKKKKKKKKKK..",
            ".KhhhhhhhhhhhhK.",
            ".KhHHhHHhHHhHhK.",
            ".KhHHhHHhHHhHhK.",
            ".KhHHhHKKHHhHhK.",
            ".KhHHhKCCKHhHhK.",
            ".KhHHKCpCCKhHhK.",
            ".KhHKCpWWpCKHhK.",
            ".KhHKCpWWpCKHhK.",
            ".KhHHKCppCKhHhK.",
            ".KhHHhKCCKHhHhK.",
            ".KhHHhHKKHHhHhK.",
            ".KhHHhHHhHHhHhK.",
            ".KhHHhHHhHHhHhK.",
            ".KhhhhhhhhhhhhK.",
            ".KKKKKKKKKKKKKK.",
        ]))
    return p


def crops():
    p = Pic(fill=PALETTE['t'])
    for y in range(16):
        for x in range(16):
            if y % 4 == 0:
                p.set(x, y, 'H')
            if y % 4 == 2 and x % 3 == 1:
                p.set(x, y, 'E')
                p.set(x, y - 1, 'e')
    return p


# The land's tiles, in the order of their numbers in lantern.aer (T_GRASS = 0, ...)
def make_tiles():
    g = grass()
    sw = swamp()
    bl = blight()
    dfl = dungeon_floor(0)
    tiles = [
        ('grass', g),
        ('grass2', grass('grass2', 18)),
        ('flowers', on(grass('flowers', 6), check('flowers', [
            "................", "..W.............", ".WYW......p.....", "..W......pYp....",
            "..........p.....", "................", "......r.........", ".....rYr........",
            "......r.....W...", "...........WYW..", "............W...", "..p.............",
            ".pYp............", "..p.....W.......", "........e.......", "................"]))),
        ('sand', sand()),
        ('road', road()),
        ('water0', water(0)), ('water1', water(1)), ('water2', water(2)),
        ('shore0', water(0, True)), ('shore1', water(1, True)), ('shore2', water(2, True)),
        ('bridge', bridge(False)),
        ('bridgev', bridge(True)),
        ('tree', on(g, TREE)),
        ('pine', on(g, PINE)),
        ('bush', on(g, BUSH)),
        ('rock', on(g, ROCK)),
        ('mount', mountain()),
        ('cliff', cliff()),
        ('swamp', sw),
        ('reeds', reeds()),
        ('deadtree', on(sw, DEADTREE)),
        ('blight', bl),
        ('blightree', on(bl, DEADTREE, {'h': 'Q', 'H': 'P'})),
        ('roofl', roof(False)),
        ('roofr', roof(True)),
        ('wallwin', wall('window')),
        ('walldoor', wall('door')),
        ('winlit', wall('lit')),
        ('burnroof', burnt_roof()),
        ('burnwall', wall('burnt')),
        ('well', well()),
        ('fence', on(g, FENCE)),
        ('plaza', plaza()),
        ('ruinfl', ruin_floor()),
        ('ruinwall', on(ruin_floor(), RUINWALL)),
        ('cave', cave()),
        ('gate', gate(False)),
        ('gateopen', gate(True)),
        ('dfloor', dfl),
        ('dfloor2', dungeon_floor(1)),
        ('dwalltop', dungeon_wall_top()),
        ('dwall', dungeon_wall()),
        ('dtorch0', torch(0)),
        ('dtorch1', torch(1)),
        ('stairs', stairs()),
        ('shrine', on(plaza(), SHRINE)),
        ('grave', on(g, GRAVE)),
        ('fire0', campfire(0)),
        ('fire1', campfire(1)),
        ('lfloor', lair_floor()),
        ('lwalltop', lair_wall_top()),
        ('lwall', lair_wall()),
        ('crops', crops()),
    ]
    return tiles


# ---- people -----------------------------------------------------------------------------------
#
# Everyone is drawn from the same three grids (from the front, from behind, from the side,
# facing right; the game mirrors that for facing left), in which the digits stand for parts of
# a person rather than colours:
#
#     1 hair or hood    2 its shadow    3 shirt    4 shirt in shadow    5 trousers or robe
#     6 boots           S skin          s skin in shadow
#
# A person is those grids with the digits coloured in, plus a few extras (a beard, a hat, a
# robe down to the ground). Each view has a standing picture and a stepping one, and the game
# swaps between them as they walk.

FRONT = check('front', [
    "................",
    ".....KKKKKK.....",
    "....K111111K....",
    "...K11111111K...",
    "...K1SSSSSS1K...",
    "...KSKSSSSKSK...",
    "...KsSSSSSSsK...",
    "....KKSSSSKK....",
    "...K43333334K...",
    "..KS43333334SK..",
    "..KK43333334KK..",
    "...K45555554K...",
    "...K55555555K...",
    "...K55KKKK55K...",
    "...K66K..K66K...",
    "....KK....KK....",
])
FRONT_STEP = ["...K55555555K...", "...K55KKKK55K...", "...K66K..KKKK...", "....KK.........."]

BACK_HEAD = ["...K11111111K...", "...K11111111K...", "...K21111112K...", "....KK2222KK...."]

SIDE = check('side', [
    "................",
    ".....KKKKK......",
    "....K11111K.....",
    "...K1111111K....",
    "...K111SSSSK....",
    "...K11SSSKSK....",
    "...K1sSSSSSK....",
    "....KKsSSSK.....",
    "....K433333K....",
    "....K4333SSK....",
    "....K44333KK....",
    "....K455555K....",
    "....K55555K.....",
    "....K55K55K.....",
    "....K66K66K.....",
    ".....KK.KK......",
])
SIDE_STEP = ["....K55555K.....", "...K55KK55K.....", "..K66K..K66K....", "..KK.....KK....."]


def person_views(colours, extras=()):
    """The six pictures of a person: front, front stepping, back, back stepping, side, side
    stepping."""
    front = [r for r in FRONT]
    front_step = front[:12] + FRONT_STEP
    back = front[:4] + BACK_HEAD + front[8:]
    back_step = back[:12] + FRONT_STEP
    side = [r for r in SIDE]
    side_step = side[:12] + SIDE_STEP
    views = {'front': front, 'front_step': front_step, 'back': back, 'back_step': back_step,
             'side': side, 'side_step': side_step}
    pics = []
    for name in ('front', 'front_step', 'back', 'back_step', 'side', 'side_step'):
        rows = [list(r) for r in views[name]]
        for extra in extras:
            extra(name, rows)
        pics.append(Pic().grid([''.join(r) for r in rows], colours))
    return pics


def put(rows, y, x, text):
    for i, ch in enumerate(text):
        if ch != ' ' and 0 <= x + i < 16:
            rows[y][x + i] = ch


def beard(view, rows):
    if view.startswith('front'):
        put(rows, 6, 4, "wWWWWWWw")
        put(rows, 7, 5, "wWWWWw")
        put(rows, 8, 6, "WWWW")
        put(rows, 9, 7, "ww")
    elif view.startswith('side'):
        put(rows, 6, 7, "WWWW")
        put(rows, 7, 7, "wWW")
        put(rows, 8, 8, "w")


def robe(view, rows):
    if view.startswith('side'):
        rows[12:16] = [list("....K555555K...."), list("...K5555555K...."),
                       list("...K5555555K...."), list("...KKKKKKKKK....")]
        if view.endswith('step'):
            rows[14] = list("..K55555555K....")
            rows[15] = list("..KKKKKKKKKK....")
    else:
        rows[12:16] = [list("...K55555555K..."), list("...K55555555K..."),
                       list("..K5555555555K.."), list("..KKKKKKKKKKKK..")]
        if view.endswith('step'):
            rows[14] = list("..K555555555K...")
            rows[15] = list("..KKKKKKKKKK....")


def long_hair(view, rows):
    if view.startswith('front'):
        for y in (8, 9, 10):
            rows[y][3] = '1'
            rows[y][12] = '1'
    elif view.startswith('back'):
        for y in (8, 9, 10):
            for x in range(4, 12):
                rows[y][x] = '1'
    else:
        for y in (8, 9, 10):
            rows[y][4] = '1'


def hood_peak(view, rows):
    put(rows, 0, 6, "KKKK")
    rows[1] = list(".....K1111K.....") if not view.startswith('side') else list("....K11111K.....")


def hat(view, rows):
    """A wide brimmed hat, in the hair's colours."""
    rows[0] = list("....KKKKKKK.....")
    rows[1] = list("....K22222K.....")
    rows[2] = list("..KKKKKKKKKKK...") if view.startswith('side') else list("..KKKKKKKKKKKK..")
    rows[3] = list("..K1111111111K..") if not view.startswith('side') else list("..K111111111K...")


def helmet(view, rows):
    rows[1] = list(".....KKKKKK.....") if not view.startswith('side') else list(".....KKKKK......")
    if view.startswith('front'):
        put(rows, 2, 5, "1W1111")
        put(rows, 4, 7, "22")
        put(rows, 5, 7, "22")


def apron(view, rows):
    if view.startswith('front'):
        for y in range(9, 13):
            put(rows, y, 5, "tttttt")
    elif view.startswith('side'):
        for y in range(9, 12):
            put(rows, y, 8, "tt")


def mask(view, rows):
    if view.startswith('front'):
        put(rows, 6, 4, "kkkkkkkk")
    elif view.startswith('side'):
        put(rows, 6, 5, "kkkkkk")


def lantern_at_hip(view, rows):
    """The player's lantern, hanging from the belt."""
    if view.startswith('front'):
        put(rows, 10, 12, "KK")
        put(rows, 11, 12, "Ky")
        put(rows, 12, 12, "KK")
    elif view.startswith('side'):
        put(rows, 10, 10, "KK")
        put(rows, 11, 10, "yK")
        put(rows, 12, 10, "KK")


def glowing_eyes(colour):
    def eyes(view, rows):
        if view.startswith('front'):
            rows[5][5] = colour
            rows[5][10] = colour
        elif view.startswith('side'):
            rows[5][9] = colour
    return eyes


def short(view, rows):
    """A child: the same person, three rows shorter (two of body, one of legs)."""
    del rows[13]
    del rows[11]
    del rows[9]
    rows[0:0] = [list("................") for _ in range(3)]


def bones(view, rows):
    """A skeleton's ribs and empty eyes."""
    if view.startswith('front') or view.startswith('back'):
        for y in (8, 10):
            put(rows, y, 4, "wKwKwKwK")
    if view.startswith('front'):
        put(rows, 5, 4, "KK")
        put(rows, 5, 10, "KK")
        put(rows, 7, 6, "KwwK")


# Everyone's colours: the digits of the grids, as palette letters
PEOPLE = [
    # the player: a green hood and cape, a cream tunic, a lantern at the belt
    ('player', {'1': 'E', '2': 'F', '3': 'm', '4': 'T', '5': 'H', '6': 'h'}, [hood_peak, lantern_at_hip]),
    ('elder', {'1': 'w', '2': 'g', '3': 'P', '4': 'Q', '5': 'P', '6': 'Q'}, [beard, robe]),
    ('smith', {'1': 'h', '2': 'K', '3': 'g', '4': 'G', '5': 'h', '6': 'K'}, [apron]),
    ('farmer', {'1': 'O', '2': 't', '3': 'b', '4': 'B', '5': 'b', '6': 't'}, [long_hair, robe]),
    ('child', {'1': 'H', '2': 'h', '3': 'r', '4': 'R', '5': 'B', '6': 't'}, [short]),
    ('merchant', {'1': 'D', '2': 'R', '3': 'Y', '4': 'O', '5': 'N', '6': 'h'}, [hat]),
    ('guard', {'1': 'w', '2': 'g', '3': 'B', '4': 'N', '5': 'G', '6': 'K'}, [helmet]),
    ('sage', {'1': 'N', '2': 'K', '3': 'N', '4': 'K', '5': 'N', '6': 'K'}, [hood_peak, beard, robe]),
    ('traveller', {'1': 't', '2': 'h', '3': 'g', '4': 'G', '5': 't', '6': 'h'}, [hood_peak]),
    ('skeleton', {'1': 'W', '2': 'w', 'S': 'W', 's': 'w', '3': 'w', '4': 'K', '5': 'w', '6': 'w'}, [bones]),
    ('bandit', {'1': 'R', '2': 'D', '3': 'G', '4': 'k', '5': 'h', '6': 'K'}, [mask]),
]


# ---- monsters ---------------------------------------------------------------------------------
#
# The ones that aren't people are drawn as two pictures each, facing right.

SLIME = [check('slime0', [
    "................", "................", "................", "................",
    "................", "......KKKK......", "....KKeeeeKK....", "...KeeLLeeeeK...",
    "..KeeLLeeeeeeK..", "..KeeeeeeeeeeK..", ".KeeeKeeeKeeeeK.", ".KEeeKeeeKeeeEK.",
    ".KEEeeeeeeeeEEK.", ".KFEEEEEEEEEEFK.", "..KKFFFFFFFFKK..", "....KKKKKKKK....",
]), check('slime1', [
    "................", "................", "................", "................",
    "................", "................", "......KKKK......", "...KKKeeeeKKK...",
    "..KeeLLeeeeeeK..", ".KeeLLeeeeeeeeK.", ".KeeeKeeeeKeeeK.", "KEeeeKeeeeKeeeEK",
    "KEEeeeeeeeeeeEEK", "KFEEEEEEEEEEEEFK", ".KKFFFFFFFFFFKK.", "..KKKKKKKKKKKK..",
])]

WOLF = [check('wolf0', [
    "................", "................", "................", "...........K.K..",
    "..........KgKgK.", "..........KgggK.", "K........KgggrKK", "KgK.KKKKKgggggWK",
    ".KgKggggggggKKK.", "..KgggggggggK...", "..KgwwwwwggggK..", "..KGgggggGGggK..",
    "..KgK.KgK.KgKK..", "..KgK.KgK..KgK..", "..KK..KK....KK..", "................",
]), check('wolf1', [
    "................", "................", "...........K.K..", "..........KgKgK.",
    "..........KgggK.", "K........KgggrKK", "KgK......KgggggWK"[:16], ".KgKKKKKKgggKKK.",
    "..KggggggggggK..", "..KgggggggggK...", "..KgwwwwwggggK..", "..KGgggggGGgK...",
    ".KgK..KgK.KgK...", "KgK....KgK..KgK.", "KK......KK...KK.", "................",
])]

BAT = [check('bat0', [
    "................", "................", ".K............K.", ".KK..........KK.",
    ".KQK...KK...KQK.", ".KQQK.KQQK.KQQK.", ".KQQQKQQQQKQQQK.", "..KQQQQrrQQQQK..",
    "...KQQQQQQQQK...", "....KQQWWQQK....", ".....KQQQQK.....", "......KKKK......",
    "................", "................", "................", "................",
]), check('bat1', [
    "................", "................", "................", "................",
    "................", ".......KK.......", "......KQQK......", "..KKKKQrrQKKKK..",
    ".KQQQQQQQQQQQQK.", "KQQQKQQWWQQKQQQK", "KQQK.KQQQQK.KQQK", "KQK...KKKK...KQK",
    "KK............KK", "................", "................", "................",
])]

WISP = [check('wisp0', [
    "................", ".......K........", "......KCK.......", "......KCK.K.....",
    ".....KCCCKCK....", "....KCCWCCCK....", "...KCCWWWCCCK...", "...KCWWWWWWCK...",
    "..KCWKWWWKWWCK..", "..KCWKWWWKWWCK..", "..KCWWWWWWWWCK..", "..KbCWWWWWWCbK..",
    "...KbCCWWCCbK...", "....KbbCCbbK....", ".....KKbbKK.....", ".......KK.......",
]), check('wisp1', [
    "................", "................", ".........K......", "....K...KCK.....",
    "...KCK..KCK.....", "....KCKKCCCK....", "....KCCWWCCK....", "...KCWWWWWCCK...",
    "..KCWKWWWKWWCK..", "..KCWKWWWKWWCK..", "..KCWWWWWWWWCK..", "..KbCWWWWWWCbK..",
    "...KbCCWWCCbK...", "...KbbCCCbbK....", "....KKbbbKK.....", "......KKK.......",
])]

DRAKE = [check('drake0', [
    "................", "................", "................", "................",
    "..........KKKK..", ".........KrrrrK.", "........KrrKrYK.", "K......KrrrrrrrK",
    "rK..KKKKrrrOOOK.", ".KrKrrrrrrrKKK..", "..KrrOOOOOrrK...", "..KrrrrrrrRRK...",
    "...KRrKKRrKRK...", "...KrK..KrKKrK..", "...KK....KK.KK..", "................",
]), check('drake1', [
    "................", "................", "................", "..........KKKK..",
    ".........KrrrrK.", "........KrrKrYK.", "rK.....KrrrrrrrK", ".KK.KKKKrrrOOOK.",
    "..KrrrrrrrrKKK..", "..KrrOOOOOrrK...", "..KrrrrrrrRRK...", "..KRrKKRrKRK....",
    ".KrK...KrKKrK...", ".KK.....KK.KK...", "................", "................",
])]


# ---- things -----------------------------------------------------------------------------------

THINGS = {
    'chest': [
        "................", "................", "................", "................",
        "...KKKKKKKKKK...", "..KtTTTTTTTTtK..", "..KtTTTTTTTTtK..", "..KhhhhYYhhhhK..",
        "..KKKKKYyKKKKK..", "..KtTTTKKTTTtK..", "..KtTTTTTTTTtK..", "..KtTTTTTTTTtK..",
        "..KhhhhhhhhhhK..", "...KKKKKKKKKK...", "................", "................"],
    'chestopen': [
        "................", "...KKKKKKKKKK...", "..KhTTTTTTTThK..", "..KhhhhhhhhhhK..",
        "..KKKKKKKKKKKK..", "..KkkkkkkkkkkK..", "..KkKkkkkkkKkK..", "..KhhhhhhhhhhK..",
        "..KKKKKKKKKKKK..", "..KtTTTTTTTTtK..", "..KtTTTTTTTTtK..", "..KtTTTTTTTTtK..",
        "..KhhhhhhhhhhK..", "...KKKKKKKKKK...", "................", "................"],
    'crate': [
        "................", "................", "................", "...KKKKKKKKKK...",
        "...KTtTTTTtTK...", "...KtKttttKtK...", "...KTtKTTKtTK...", "...KTTtKKtTTK...",
        "...KTTtKKtTTK...", "...KTtKTTKtTK...", "...KtKttttKtK...", "...KTtTTTTtTK...",
        "...KhhhhhhhhK...", "...KKKKKKKKKK...", "................", "................"],
    'coin': [
        "................", "................", "................", "................",
        "................", "......KKKK......", ".....KYyyYK.....", "....KYyYYYOK....",
        "....KYyYYYOK....", "....KYYYYYOK....", "....KYYYYOOK....", ".....KOOOOK.....",
        "......KKKK......", "................", "................", "................"],
    'heart': [
        "................", "................", "................", "................",
        "....KKK..KKK....", "...KrWrKKrrrK...", "...KrrrrrrrRK...", "...KrrrrrrrRK...",
        "....KrrrrrRK....", ".....KrrrRK.....", "......KrRK......", ".......KK.......",
        "................", "................", "................", "................"],
    'potion': [
        "................", "................", "......KKKK......", "......KttK......",
        ".......KK.......", "......KWbK......", ".....KWbbbK.....", "....KbWrrrbK....",
        "....KbrrrrRK....", "....KbrrrrRK....", "....KbrRrRRK....", ".....KbRRRK.....",
        "......KKKK......", "................", "................", "................"],
    'sword': [
        "................", "............KK..", "...........KWK..", "..........KWwK..",
        ".........KWwK...", "........KWwK....", ".......KWwK.....", "..KK..KWwK......",
        "..KYKKWwK.......", "...KYYwK........", "....KYYK........", "...KhKYYK.......",
        "..KhK.KYK.......", ".KhK...KK.......", ".KK.............", "................"],
    'sunstone': [
        "................", ".......K........", "...K..KYK..K....", "....KKYyYKK.....",
        "....KYyWyYK.....", "..KKYyWWWyYKK...", "..KYYyWWWyYYK...", "...KKYyyyYKK....",
        "....KYYOYYK.....", "...K.KYOYK.K....", ".......K........", "................",
        "................", "................", "................", "................"],
    'bell': [
        "................", ".......KK.......", "......KwWK......", ".....KwWWwK.....",
        "....KwWWWwgK....", "....KwWWwwgK....", "....KwWwwggK....", "...KwWwwwggGK...",
        "...KwwwwggGGK...", "..KKKKKKKKKKKK..", "..KwwwwwwwggGK..", "...KKKKKKKKKK...",
        "......KwgK......", ".......KK.......", "................", "................"],
    'arrow': [
        "................", "................", ".............KK.", "............KCK.",
        "...........KCWK.", "..........KCWK..", ".........KtCK...", "........KtK.....",
        ".......KtK......", "......KtK.......", ".....KtK........", "..K.KtK.........",
        "..KWtK..........", "...KWK..........", "..KWKWK.........", "...K.K.........."],
    'seal': [
        "................", "................", ".....KKKKKK.....", "....KrrrrrRK....",
        "...KrrYrYrYRK...", "...KrrYYYYYRK...", "...KrrYYYYYRK...", "...KrrrrrrRRK...",
        "...KrRrrrRRRK...", "....KRRRRRRK....", ".....KKKKKK.....", "......KYYK......",
        ".....KYK.KYK....", ".....KK...KK....", "................", "................"],
    'locket': [
        "................", ".....KK..KK.....", "....K..KK..K....", "....K......K....",
        ".....K....K.....", "......K..K......", "......KKKK......", ".....KYYYYK.....",
        "....KYyYYOYK....", "....KYYppYOK....", "....KYpPPpOK....", ".....KYppOK.....",
        "......KYOK......", ".......KK.......", "................", "................"],
    'charm': [
        "................", "................", "......KKKK......", ".....K....K.....",
        "......K..K......", ".......KK.......", "......KEEK......", ".....KEeLEK.....",
        "....KEeLLeEK....", "....KEeeeEFK....", ".....KEeEFK.....", "......KEFK......",
        ".......KK.......", "................", "................", "................"],
    'spark0': [
        "................", "................", "................", "................",
        ".......W........", ".......y........", "....W.yWy.W.....", ".....yWWWy......",
        "...WyWWWWWyW....", ".....yWWWy......", "....W.yWy.W.....", ".......y........",
        ".......W........", "................", "................", "................"],
    'spark1': [
        "................", "................", "....W.....W.....", ".....y...y......",
        "................", "................", "........y.......", ".......yWy......",
        "........y.......", "................", "................", ".....y...y......",
        "....W.....W.....", "................", "................", "................"],
    'poof0': [
        "................", "................", "................", "......wwww......",
        "....wwWWWWww....", "...wWWWWWWWWw...", "...wWWwwwwWWw...", "..wWWw....wWWw..",
        "..wWWw....wWWw..", "...wWWwwwwWWw...", "...wWWWWWWWWw...", "....wwWWWWww....",
        "......wwww......", "................", "................", "................"],
    'poof1': [
        "................", "..w..........w..", "...w...ww...w...", "................",
        "..w..........w..", "................", ".w............w.", "................",
        "................", ".w............w.", "................", "..w..........w..",
        "................", "...w...ww...w...", "..w..........w..", "................"],
    'fireball': [
        "................", "................", "................", "................",
        "......yOO.......", ".....yWyOO......", "....yWWyOrr.....", "....yWWyOrrD....",
        "....OyyOrrD.....", ".....OOrrD......", "......rrD.......", "................",
        "................", "................", "................", "................"],
    'mark': [
        "................", "......KKKK......", ".....KYYYYK.....", ".....KYyyYK.....",
        ".....KYyyYK.....", "......KYYK......", "......KYYK......", "......KYYK......",
        ".......KK.......", "......KYYK......", "......KYYK......", ".......KK.......",
        "................", "................", "................", "................"],
    'sign': [
        "................", "................", "..KKKKKKKKKKKK..", ".KTTTTTTTTTTTTK.",
        ".KTtttTTtttTTTK.", ".KTTTTTTTTTTTTK.", ".KTttTTtttTTtTK.", ".KTTTTTTTTTTTTK.",
        "..KKKKKtHKKKKK..", "......KtHK......", "......KtHK......", "......KtHK......",
        ".....KttHHK.....", "......KKKK......", "................", "................"],
    'bones': [
        "................", "................", "................", "................",
        "................", "................", "................", "......KKKK......",
        ".....KWWWwK.....", ".....KWKKwK.....", "......KwwK......", "..KK..KKKK..KK..",
        "..KWKKKWWKKKWK..", "...KWWWwwWWWK...", "..KWKKKKKKKKWK..", "..KK........KK.."],
}

SLASH = {
    # the sword's swing, drawn next to the player in the way they face
    'slash_down': [
        "................", "................", "....K......K....", "...KWK....KWK...",
        "...KWWKKKKWWK...", "....KWWWWWWK....", ".....KWwwWK.....", "......KwwK......",
        ".......KK.......", "................", "................", "................",
        "................", "................", "................", "................"],
    'slash_up': [
        "................", "................", "................", "................",
        "................", "................", "................", ".......KK.......",
        "......KwwK......", ".....KWwwWK.....", "....KWWWWWWK....", "...KWWKKKKWWK...",
        "...KWK....KWK...", "....K......K....", "................", "................"],
    'slash_side': [
        "................", "...KK...........", "...KWK..........", "....KWK.........",
        ".....KWK........", "......KWK.......", "......KWWK......", "......KwWK......",
        "......KwWK......", "......KWWK......", "......KWK.......", ".....KWK........",
        "....KWK.........", "...KWK..........", "...KK...........", "................"],
}


def make_sprites():
    """Everything that moves or can be picked up, in the order of their numbers in
    lantern.aer (S_PLAYER = 0, ...)."""
    sprites = []
    for name, colours, extras in PEOPLE:
        for i, pic in enumerate(person_views(colours, extras)):
            sprites.append((f'{name}{i}', pic))
    for name, frames in (('slime', SLIME), ('wolf', WOLF), ('bat', BAT), ('wisp', WISP),
                         ('drake', DRAKE)):
        for i, rows in enumerate(frames):
            sprites.append((f'{name}{i}', Pic().grid(rows)))
    for name, rows in list(SLASH.items()) + list(THINGS.items()):
        sprites.append((name, Pic().grid(check(name, rows))))
    return sprites


# ---- the villains, twice as big ----------------------------------------------------------------
#
# Each villain is drawn 32 x 32. They stand facing you, so only the left half is drawn and
# mirrored for the right; what they hold (a staff, a flame, a sword) is drawn over that.

def symmetric(name, half):
    """A 32 wide grid from its left half, mirrored."""
    check(name, half)
    return [row + row[::-1] for row in half]


HOLLOW = symmetric('hollow', [
    "................", "...........K..K.", "...........KWKKW", ".........KKKWwKW",
    "........KkkkKKKK", ".......KkkkkkkkK", "......KkkkKKKKKK", "......KkkKwwwwww",
    ".....KkkKwWWWWWW", ".....KkkKwWKKKWW", ".....KkkKwKCCCKW", ".....KkkKwWKKKWW",
    ".....KkkKKwWWWWK", ".....KkkkKKwKwKw", "....KkkkkkKKKKKK", "....KQQkkkkkkkkk",
    "...KQQQQkkkkkkkQ", "...KQPQQQQQQQQQQ", "..KQPQQQQQQQQQPQ", "..KQPQQKQQQQQQPQ",
    "..KwwKQKQQQQQQPQ", "..KWwKQKQQQQQQPQ", "...KKKQQKQQQQQPQ", "....KQQQKQQQQQPQ",
    "....KQQQQKQQQQPQ", "...KQQQQQKQQQQPQ", "...KQPQQQQKQQQPQ", "..KQQPQQQQKQQQPQ",
    "..KQQPQQQQQKQQPQ", ".KQQKPQQKQQKQQPQ", ".KKK.KKK.KKK.KKK", "................",
])
# the Hollow's staff, with a glowing orb, in his right hand (on the right of the picture)
HOLLOW_STAFF = [(1, 27, "KKK"), (2, 26, "KpPpK"), (3, 26, "KCWCK"), (4, 26, "KpCpK"),
                (5, 26, "KPpPK")] + [(y, 27, "KtK") for y in range(6, 20)] +                [(y, 28, "t") for y in range(22, 30)]

WITCH = symmetric('witch', [
    "................", "..............KK", ".............KDD", "............KDDD",
    "...........KDRDD", "..........KDDRDD", "..........KDDDDD", ".........KDYYYYY",
    "....KKKKKKKKKKKK", "...KDDDDDDDDDDDD", "....KKOOKKKKKKKK", ".....KOOKSSSSSSS",
    ".....KOOKSKYSSSS", ".....KOOKSSSSSSs", ".....KOOOKSSSSSS", "....KOOOOKKSSSSK",
    "....KOOODKKKKKKK", "...KOOOKDDDDDDDD", "...KOOKDDRDDDDDD", "...KOKDDDRDDDDDY",
    "..KSSKDDDRDDDDDY", "..KSSKDDDRDDDDDY", "...KKDDDDRDDDDDY", "....KDDDDRDDDDDY",
    "....KDDDDDRDDDDY", "...KDDDDDDRDDDDY", "...KDDDDDDRDDDDY", "..KDDDDDDDDRDDDY",
    "..KDDDRDDDDRDDDY", ".KDDDRDDDDDDRDDY", ".KKKKKKKKKKKKKKK", "................",
])
# her hat's tip bends over to the right, and a flame burns over her right hand
WITCH_FLAME = [(0, 15, "KKKK"), (1, 17, "KDK"), (2, 18, "K"),
               (14, 27, "y"), (15, 26, "yYy"), (16, 25, "yYOYy"), (17, 25, "YOrOY"),
               (18, 26, "OrO"), (19, 27, "D")]

WOLF_LORD = symmetric('wolflord', [
    "................", "........KK......", "........KgK.....", "........KggK....",
    "........KgwgKKKK", "........KgggGGGG", ".......KggGGGGGG", ".......KgGGGGGGG",
    ".......KgGGKrKGG", ".......KgGGGGGGG", "......KggwGGGGww", "......KgwwwGGwww",
    ".......KwwwwKwwW", "........KKwwWKKK", ".......KDKKWKWKW", "....KKDDDgKKKKKK",
    "...KDDDgggwgggww", "..KDDDggwgGGGGGG", "..KDDKgwgGGkGGGG", "..KDDKggGGkGGGGG",
    ".KDDKgwKGGkGGGGG", ".KDDKgwKGGkGGGGG", ".KDDKgWKGGkGGGGG", ".KDDKKWKGGkGGGGG",
    ".KDDDKKKGGkkkkkk", ".KDDDDKGGGGKGGGG", ".KDDDDKGGGGKGGGG", "..KDDDKGGGGKGGGG",
    "..KDDDKkkkKKKkkk", "...KDKKkkkK.KkkK", "....K.KKKKK.KKKK", "................",
])

BARON = symmetric('baron', [
    "................", ".....KK.........", "....KWK.........", "....KWWK........",
    ".....KWWKKKKKKKK", "......KWWKgggggg", "......KKwggwwwww", ".......KgggwWWWW",
    ".......KgGgggggg", ".......KgKKKKKKK", ".......KgKKKKrKK", ".......KgGgggggg",
    ".......KGgggYggg", "........KGGGGGGG", "...KKKKKKKKKKKKK", "..KRKwwwwgKggggg",
    ".KRKwWWwwgKgYggg", ".KRKwwwwggKgYggg", ".KRRKKKKKKgggYgg", ".KRRKgggKggggYYY",
    ".KRRKgwgKgggggKK", ".KRRKgwgKgggggKG", ".KRRKgwgKggggKGG", ".KRRKKgKKgggKGGG",
    ".KRRKSSKKYYYYYYY", ".KRRRKKKgGGGKGGG", ".KRRRRKgGGGGKGGG", "..KRRRKgGGGGKGGG",
    "..KRRRKgGGGKKKGG", "...KRRKKKKKK.KKK", "....KKKkkkkK.Kkk", "................",
])
# a greatsword held point up in his right hand
BARON_SWORD = [(5, 28, "K"), (6, 27, "KWK")] + [(y, 27, "KwK") for y in range(7, 22)] +               [(22, 25, "KYYYYK"), (23, 27, "KhK"), (25, 27, "KhK"), (26, 27, "KYK")]


def boss(rows, extras, aura):
    """A villain's two pictures: the aura (a glowing outline) dim, then bright with the
    villain a pixel higher, as if breathing."""
    base = Pic(32, 32).grid(check('boss', rows, 32))
    for y, x, part in extras:
        base.grid([part], dx=x, dy=y)
    pics = []
    for frame in range(2):
        p = Pic(32, 32)
        for y in range(32):
            for x in range(32):
                if y + frame < 32:
                    p.px[y][x] = base.px[y + frame][x]
        # every see-through pixel next to the villain, every other one, glows
        glow = [(x, y) for y in range(32) for x in range(32) if p.px[y][x] is None and
                any(0 <= x + dx < 32 and 0 <= y + dy < 32 and p.px[y + dy][x + dx] is not None
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        for x, y in glow:
            if (x + y + frame) % 2 == 0:
                p.set(x, y, aura[frame])
        pics.append(p)
    return pics


def make_bosses():
    """The villains, in the order of the threats in lantern.aer: the Hollow, the Ember Witch,
    the Wolf Lord and the Iron Baron."""
    return (boss(HOLLOW, HOLLOW_STAFF, ('Q', 'P')) + boss(WITCH, WITCH_FLAME, ('R', 'O')) +
            boss(WOLF_LORD, [], ('D', 'r')) + boss(BARON, BARON_SWORD, ('t', 'Y')))


# ---- the little signs on the screen -----------------------------------------------------------

HUD = {
    'heart': ["........", ".KK.KK..", "KrWKrrK.", "KrrrrRK.", ".KrrRK..", "..KRK...", "...K....", "........"],
    'half': ["........", ".KK.KK..", "KrWKGGK.", "KrrkGGK.", ".KrkGK..", "..KkK...", "...K....", "........"],
    'empty': ["........", ".KK.KK..", "KGGKGGK.", "KGGGGGK.", ".KGGGK..", "..KGK...", "...K....", "........"],
    'coin': ["........", "..KKKK..", ".KYyYOK.", ".KyYYOK.", ".KYYYOK.", ".KYOOOK.", "..KKKK..", "........"],
    'potion': ["...KK...", "...tt...", "..KWbK..", ".KbrrrK.", ".KrrrRK.", ".KrRRRK.", "..KKKK..", "........"],
    'sun': ["...Y....", ".Y.y.Y..", "..yYYy..", "YyYWYyY.", "..yYYy..", ".Y.y.Y..", "...Y....", "........"],
    'moon': ["..KKK...", ".KwWK...", "KwWK....", "KwWK....", "KwWWK.KK", ".KwWWWwK", "..KKKKK.", "........"],
    'sword': [".....KK.", "....KWK.", "...KWK..", "K.KWK...", "KYwK....", ".KYK....", "KhKYK...", "KK......"],
    'relic': ["...K....", "..KCK...", ".KCWCK..", "KCWWWCK.", ".KCWCK..", "..KCK...", "...K....", "........"],
    'locket': ["..K..K..", "...KK...", "..KYYK..", ".KYppYK.", ".KYPpYK.", "..KYYK..", "...KK...", "........"],
    'charm': ["...KK...", "..K..K..", "...KK...", "..KEeK..", ".KEeLEK.", "..KEEK..", "...KK...", "........"],
    'skull': ["..KKKK..", ".KWWWWK.", "KWKWWKWK", "KWKWWKWK", ".KWWWWK.", "..KWKWK.", "..KKKK..", "........"],
    'arrow': ["...K....", "...KK...", "KKKKYK..", "KYYYYYK.", "KKKKYK..", "...KK...", "...K....", "........"],
    'dot': ["........", "........", "...KK...", "..KYYK..", "..KYYK..", "...KK...", "........", "........"],
}

# ---- the title --------------------------------------------------------------------------------
#
# LANTERN in letters 7 pixels tall, filled with a fire's colours from pale yellow at the top
# down to red, inside a dark outline.

LETTERS = {
    'L': ["11....", "11....", "11....", "11....", "11....", "111111", "111111"],
    'A': ["..11..", ".1111.", "11..11", "11..11", "111111", "11..11", "11..11"],
    'N': ["11...11", "111..11", "1111.11", "11.1111", "11..111", "11...11", "11...11"],
    'T': ["111111", "111111", "..11..", "..11..", "..11..", "..11..", "..11.."],
    'E': ["111111", "11....", "11....", "11111.", "11....", "111111", "111111"],
    'R': ["11111.", "11..11", "11..11", "11111.", "11.11.", "11..11", "11..11"],
}


def logo():
    word = 'LANTERN'
    gap = 2
    width = sum(len(LETTERS[c][0]) for c in word) + gap * (len(word) - 1) + 4
    p = Pic(width, 11)
    fire = ['y', 'y', 'Y', 'Y', 'O', 'r', 'R']
    x0 = 2
    for c in word:
        rows = LETTERS[c]
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == '1':
                    p.set(x0 + x, y + 2, fire[y])
        x0 += len(rows[0]) + gap
    # the outline: every see-through pixel touching a letter goes dark
    solid = [[p.px[y][x] is not None for x in range(width)] for y in range(11)]
    for y in range(11):
        for x in range(width):
            if not solid[y][x] and any(0 <= x + dx < width and 0 <= y + dy < 11 and solid[y + dy][x + dx]
                                       for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                p.set(x, y, 'K')
    # a shadow below, a pixel down
    for x in range(width):
        if solid[8][x]:
            p.set(x, 10, 'D')
    return p


# ---- writing it all ---------------------------------------------------------------------------

def preview(name, pics, scale=6, across=12):
    """A big PNG of a sheet, `across` pictures to a row, with a checkerboard behind see-through
    parts, to look at."""
    from PIL import Image
    w, h = pics[0].w, pics[0].h
    rows = (len(pics) + across - 1) // across
    img = Image.new('RGB', (min(len(pics), across) * (w + 1) * scale, rows * (h + 1) * scale), (20, 20, 20))
    for i, p in enumerate(pics):
        x0, y0 = (i % across) * (w + 1), (i // across) * (h + 1)
        for y in range(p.h):
            for x in range(p.w):
                c = p.px[y][x] or ((60, 60, 60) if (x // 4 + y // 4) % 2 else (90, 90, 90))
                img.paste(c, ((x0 + x) * scale, (y0 + y) * scale, (x0 + x + 1) * scale, (y0 + y + 1) * scale))
    img.save(os.path.join(PREVIEW, name + '.png'))


# where "preview" puts its PNGs: the folder after it on the command line, or the current one
PREVIEW = sys.argv[sys.argv.index('preview') + 1] if 'preview' in sys.argv[:-1] else '.'


def main():
    os.makedirs(OUT, exist_ok=True)
    tiles = make_tiles()
    sprites = make_sprites()
    for light in range(4):
        write_bmp(os.path.join(OUT, f'tiles{light}.bmp'), [p for _, p in tiles], light)
        write_bmp(os.path.join(OUT, f'spr{light}.bmp'), [p for _, p in sprites], light)
    bosses = make_bosses()
    write_bmp(os.path.join(OUT, 'boss.bmp'), bosses)
    hud = [Pic(8, 8).grid(check(n, rows, 8)) for n, rows in HUD.items()]
    write_bmp(os.path.join(OUT, 'hud.bmp'), hud)
    write_bmp(os.path.join(OUT, 'logo.bmp'), [logo()])

    # the numbers, for lantern.aer (printed so they can be checked against it)
    print('tiles:', ' '.join(f'{i}={n}' for i, (n, _) in enumerate(tiles)))
    print('sprites:', ' '.join(f'{i}={n}' for i, (n, _) in enumerate(sprites)))
    print('hud:', ' '.join(f'{i}={n}' for i, n in enumerate(HUD)))

    if 'preview' in sys.argv:
        os.makedirs(PREVIEW, exist_ok=True)
        preview('tiles', [p for _, p in tiles])
        preview('sprites', [p for _, p in sprites])
        preview('bosses', bosses)
        preview('logo', [logo()], 8, 1)


if __name__ == '__main__':
    main()
