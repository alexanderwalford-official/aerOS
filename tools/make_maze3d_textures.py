#!/usr/bin/env python3
"""
make_maze3d_textures.py - make Maze 3D's dungeon pictures: stone walls, a flagstone floor and a
cobbled ceiling

    python3 tools/make_maze3d_textures.py

writes them to packages/maze3d/, where pkg install puts them in /apps/maze3d/ next to the game
(packages/maze3d.aer, which reads them with rdb and draws them with imgcol and imgrow). Nothing
is photographed or copied: every picture is worked out here, a pixel at a time, from a few
simple ideas:

    blocks     rows of stones of different widths, with mortar between them and each row's
               joints moved along from the row above (the way real walls are built)
    bevels     the top and left edge of each stone a little lighter, the bottom and right a
               little darker, as if lit from above: that is what makes them look carved
    noise      "value noise": random brightness at the corners of a coarse grid, blended
               smoothly in between, for the grain and the dirt in the stone
    cells      for the cobbles, every pixel belongs to the nearest of a scatter of points (a
               "Voronoi" pattern): the edges between them are where the gaps go

Each picture is 64 x 64 pixels (one square of the maze), and everything wraps round its edges,
so the floor and ceiling tile without seams. They are 32 bit BMPs (16 KB each) rather than PNGs:
AerOS keeps only the last PNG it decoded, so a game that draws several pictures in turn on
every frame would decode them over and over. A BMP is drawn straight from its bytes.

Running this again makes exactly the same pictures (the randomness comes from fixed seeds).
"""

import math
import os
import random
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'maze3d')

SIZE = 64   # pixels across and down: one square of the maze


# ---- helpers ------------------------------------------------------------------------------------

def clamp(value):
    """A colour part as a whole number from 0 to 255."""
    return max(0, min(255, int(round(value))))


def mix(a, b, amount):
    """Colour a moved `amount` (0 to 1) of the way towards colour b."""
    return tuple(x + (y - x) * amount for x, y in zip(a, b))


def scale(colour, amount):
    """A colour made brighter (amount above 1) or darker (below 1)."""
    return tuple(x * amount for x in colour)


def value_noise(seed, cells):
    """A function giving smooth noise from 0 to 1 at any pixel, with `cells` grid squares across
    the picture (each corner a random value). It wraps round, so the picture tiles."""
    rng = random.Random(seed)
    corners = [[rng.random() for _ in range(cells)] for _ in range(cells)]
    step = SIZE / cells

    def smooth(t):
        return t * t * (3 - 2 * t)   # eases in and out, so the grid doesn't show

    def at(x, y):
        gx, gy = (x % SIZE) / step, (y % SIZE) / step
        x0, y0 = int(gx), int(gy)
        tx, ty = smooth(gx - x0), smooth(gy - y0)
        x1, y1 = (x0 + 1) % cells, (y0 + 1) % cells
        top = corners[y0][x0] + (corners[y0][x1] - corners[y0][x0]) * tx
        bottom = corners[y1][x0] + (corners[y1][x1] - corners[y1][x0]) * tx
        return top + (bottom - top) * ty
    return at


def layered_noise(seed):
    """Coarse noise with finer noise added on top, from 0 to 1: blotches with grain in them."""
    layers = [(value_noise(seed, 4), 0.5), (value_noise(seed + 1, 8), 0.3), (value_noise(seed + 2, 16), 0.2)]
    return lambda x, y: sum(layer(x, y) * weight for layer, weight in layers)


def save_bmp(name, pixels):
    """Write a SIZE x SIZE picture (rows of (r, g, b), the top row first) as a 32 bit BMP: a 54
    byte header, then the rows bottom first, each pixel blue, green, red and a spare byte (255).
    32 bits rather than 24, as AerOS reads a 4 byte pixel in one go, which makes drawing the
    floor quicker."""
    data = bytearray()
    for row in reversed(pixels):
        for r, g, b in row:
            data += bytes((clamp(b), clamp(g), clamp(r), 255))
    header = b'BM' + struct.pack('<IHHI', 54 + len(data), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, SIZE, SIZE, 1, 32, 0, len(data), 2835, 2835, 0, 0)
    with open(os.path.join(OUT, name + '.bmp'), 'wb') as f:
        f.write(header + info + data)


# ---- walls of blocks ----------------------------------------------------------------------------

def block_layout(seed, row_height, widths):
    """Which stone each pixel is in, for a wall of rows `row_height` tall, each row made of
    stones whose widths are picked from `widths` and add up to SIZE. Gives, for every pixel,
    (stone number, x and y inside the stone, the stone's width and height)."""
    rng = random.Random(seed)
    layout = [[None] * SIZE for _ in range(SIZE)]
    stone = 0
    for top in range(0, SIZE, row_height):
        # pick widths until the row is full (the last one takes what is left), then start the
        # row at a random place, so its joints don't line up with the row above
        row = []
        while sum(row) < SIZE:
            left = SIZE - sum(row)
            pick = rng.choice(widths)
            row.append(left if left - pick < min(widths) else pick)
        shift = rng.randrange(SIZE)
        x = shift
        for width in row:
            for dx in range(width):
                for dy in range(row_height):
                    layout[top + dy][(x + dx) % SIZE] = (stone, dx, dy, width, row_height)
            x += width
            stone += 1
    return layout, stone


def block_wall(seed, colour, row_height, widths, mortar, gap=2, tint=0.12):
    """A wall of stone blocks: each block its own shade of `colour`, lighter at its top and left
    edges and darker at its bottom and right, with grainy noise, chips, and `mortar` between."""
    layout, stones = block_layout(seed, row_height, widths)
    rng = random.Random(seed + 10)
    # each stone a little lighter or darker, and a little warmer or cooler, than the others
    shades = [(rng.uniform(1 - tint, 1 + tint), rng.uniform(-8, 8)) for _ in range(stones)]
    grain = layered_noise(seed + 20)
    speckle = random.Random(seed + 30)
    picture = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            stone, dx, dy, width, height = layout[y][x]
            n = grain(x, y)
            # the mortar: the first `gap` pixels of each stone's top and left, a little rough
            edge = min(dx, dy)
            if edge < gap or (edge == gap and n < 0.35):
                row.append(scale(mortar, 0.8 + n * 0.4))
                continue
            bright, warm = shades[stone]
            c = scale(colour, bright * (0.78 + n * 0.44))
            c = (c[0] + warm, c[1], c[2] - warm)
            # the bevel: lit from the top left
            if dy <= gap or dx <= gap:
                c = scale(c, 1.18)
            elif dy >= height - 2 or dx >= width - 2:
                c = scale(c, 0.72)
            # here and there a darker pit or a lighter fleck
            s = speckle.random()
            if s < 0.04:
                c = scale(c, 0.7)
            elif s > 0.985:
                c = scale(c, 1.25)
            row.append(c)
        picture.append(row)
    return picture


def add_moss(picture, seed):
    """Moss growing on a wall: green where the noise is high, most of all near the top, and
    creeping along the mortar."""
    patches = layered_noise(seed)
    rng = random.Random(seed + 1)
    for y in range(SIZE):
        for x in range(SIZE):
            n = patches(x, y) + (1 - y / SIZE) * 0.25
            if n > 0.62:
                amount = min(1.0, (n - 0.62) * 4) * rng.uniform(0.6, 1.0)
                green = (50, 105 + rng.uniform(-20, 25), 45)
                picture[y][x] = mix(picture[y][x], green, amount)
    return picture


def add_cracks(picture, seed, count):
    """A few thin dark cracks, wandering down and across from random places."""
    rng = random.Random(seed)
    for _ in range(count):
        x, y = rng.uniform(0, SIZE), rng.uniform(0, SIZE)
        heading = rng.uniform(0, 2 * math.pi)
        for _ in range(rng.randint(6, 14)):
            px, py = int(x) % SIZE, int(y) % SIZE
            picture[py][px] = scale(picture[py][px], 0.55)
            heading += rng.uniform(-0.6, 0.6)
            x += math.cos(heading)
            y += math.sin(heading)
    return picture


# ---- the door -----------------------------------------------------------------------------------

def door():
    """A heavy wooden door in a gold frame, held together by two gold bands with rivets, and
    a ring to pull it by. The game makes it glow once every crystal is taken."""
    grain = value_noise(41, 16)
    rings = value_noise(42, 4)
    gold = (230, 178, 60)
    picture = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            plank = (x - 4) // 14   # five planks between the frame's sides
            # wood: long streaks down the plank (the noise squashed sideways), darker joints
            streak = grain(x * 4, y / 3)
            c = scale((120, 74, 40), 0.7 + streak * 0.5 + rings(x, y) * 0.15)
            if (x - 4) % 14 == 0:
                c = scale(c, 0.45)
            if plank % 2:
                c = scale(c, 0.92)
            # the gold frame round the edge, with a shadow inside it
            if x < 4 or x >= SIZE - 4 or y < 3:
                shine = 1.15 if (x == 1 or y == 1) else 0.85 if (x == 3 or x == SIZE - 2) else 1.0
                c = scale(gold, shine * (0.85 + grain(x, y) * 0.25))
            elif x < 6 or x >= SIZE - 6 or y < 5:
                c = scale(c, 0.6)
            # two bands across, with rivets
            for band in (14, 46):
                if band <= y < band + 5:
                    c = scale(gold, (1.15 if y == band else 0.8 if y == band + 4 else 1.0) * (0.85 + grain(x, y) * 0.25))
                    if (x - 9) % 11 == 0 and y == band + 2:
                        c = (255, 235, 150)
            # the ring handle: a circle of gold to the right of the middle
            d = math.hypot(x - 46, y - 32)
            if 3.0 <= d < 4.6:
                c = scale(gold, 1.1 if y < 32 else 0.8)
            row.append(c)
        picture.append(row)
    return picture


# ---- the floor and ceiling ----------------------------------------------------------------------

def flagstones():
    """A floor of big flat flagstones, two rows to a square, set apart by dark gaps, worn smooth
    in the middle and grimy at the edges."""
    layout, stones = block_layout(7, 32, [20, 24, 28, 32, 40])
    rng = random.Random(8)
    shades = [(rng.uniform(0.85, 1.12), rng.uniform(-10, 10)) for _ in range(stones)]
    grain = layered_noise(9)
    speckle = random.Random(10)
    picture = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            stone, dx, dy, width, height = layout[y][x]
            n = grain(x, y)
            edge = min(dx, dy, width - 1 - dx, height - 1 - dy)
            if min(dx, dy) < 2:
                row.append(scale((34, 30, 27), 0.8 + n * 0.4))
                continue
            bright, warm = shades[stone]
            c = scale((112, 104, 94), bright * (0.75 + n * 0.45))
            c = (c[0] + warm, c[1] + warm * 0.4, c[2] - warm * 0.5)
            # grime collects near the gaps; the middle is worn lighter by feet
            if edge < 4:
                c = scale(c, 0.78 + edge * 0.05)
            if speckle.random() < 0.05:
                c = scale(c, 0.75)
            row.append(c)
        picture.append(row)
    return add_cracks(picture, 11, 4)


def cobbles():
    """A dark ceiling of rounded cobbles: each pixel belongs to the nearest of a scatter of
    points, one in each square of a 5 x 5 grid, moved about at random. Where two points are
    nearly as near as each other is the gap between two stones; nearer a stone's own point is
    its rounded top, which is lighter."""
    rng = random.Random(21)
    grid = 5
    cell = SIZE / grid
    points = [((i + rng.uniform(0.2, 0.8)) * cell, (j + rng.uniform(0.2, 0.8)) * cell, rng.uniform(0.8, 1.15))
              for j in range(grid) for i in range(grid)]
    grain = layered_noise(22)

    def wrapped(a, b):
        d = abs(a - b) % SIZE
        return min(d, SIZE - d)   # the distance across the edge, as the picture wraps round
    picture = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            distances = sorted((math.hypot(wrapped(x + 0.5, px), wrapped(y + 0.5, py)), shade)
                               for px, py, shade in points)
            (near, shade), (next_near, _) = distances[0], distances[1]
            gap = next_near - near
            n = grain(x, y)
            if gap < 1.6:
                row.append(scale((20, 19, 22), 0.8 + n * 0.4))
                continue
            # rounded: brightest in the middle of the stone, darker towards its edge
            round_top = min(1.0, gap / 7)
            c = scale((78, 74, 76), shade * (0.55 + round_top * 0.45) * (0.8 + n * 0.4))
            row.append(c)
        picture.append(row)
    return picture


# ---- make them all ------------------------------------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)
    pictures = {
        # '#' walls: big grey-blue blocks of uneven widths
        'stone': add_cracks(block_wall(1, (118, 122, 132), 16, [12, 16, 20, 24], (40, 38, 38), tint=0.14), 2, 3),
        # 'B' walls: small dark red bricks
        'brick': block_wall(3, (150, 72, 50), 8, [16], (58, 50, 46), gap=1, tint=0.16),
        # 'G' walls: greener stone, with moss on it
        'moss': add_moss(block_wall(5, (100, 110, 96), 16, [16, 20, 24, 28], (36, 40, 34), tint=0.12), 6),
        # 'E' the gold door
        'door': door(),
        'floor': flagstones(),
        'ceiling': cobbles(),
    }
    for name, picture in pictures.items():
        save_bmp(name, picture)
        print(f'{name}.bmp')


if __name__ == '__main__':
    main()
