#!/usr/bin/env python3
"""
make_knocks_sprites.py - make the 3D models of Four Knocks (the knocks package) and render them
into the pictures the game draws: the thing that lives under the observatory, walking, listening
and lunging, seen from every side; the furniture and the things you find; and the close-up of the
thing that is the last you see when it catches you

    python3 tools/make_knocks_sprites.py

needs numpy, and writes packages/knocks/beast.bmp, lunge.bmp, props.bmp and scare.bmp.

HOW THE MODELS ARE MADE

Nothing here is drawn by hand or copied: every model is a "signed distance function", a sum that
says, for any point in space, how far it is from the model's surface (negative inside it). A
capsule (a rod with round ends) is "the distance to the line between its ends, less its radius";
a model is many of them put together, the nearest one winning ("union"), or blended into one
another where they meet ("smooth union", like flesh over bones), or cut out of one another
("subtraction", for the hollow where a face should be). The thing is about 30 such shapes on a
skeleton whose joints move with the walk.

HOW THEY ARE RENDERED

"Ray marching": from every pixel a ray goes into the scene, and steps forward by the distance the
function gives (it can't hit anything closer than that), until it is almost touching the surface,
or has gone past everything. The surface's direction there (its "normal") is how the distance
changes nearby, and lights it: here, a lamp at the viewer, like the flashlight the game shows
it by, so the edges facing away fall into darkness. numpy does every pixel's ray at once.

Pictures are rendered at twice their size and shrunk, so the edges are smooth, and are saved as
32 bit BMPs whose 4th byte is how solid each pixel is (0 nothing, 255 solid): imgcol(..., true)
draws them strip by strip, leaving out the see-through parts.

THE PICTURES

  beast.bmp   the thing walking: 5 angles (from in front, then turning 45 degrees at a time to
              behind) x 8 frames of its walk, then 5 angles x 2 frames of it standing still with
              its head cocked, listening. Each frame is 72 x 96 pixels (one square of the map is
              96 pixels tall, 72 wide), side by side in one long row. Seen from its other side
              (225 to 315 degrees) the game draws a frame mirrored.
  lunge.bmp   4 frames of it lunging, from in front
  props.bmp   18 things, 64 x 64 each (one square), from in front and a little above
  scare.bmp   a close-up, 320 x 240, for the moment it catches you

Running this again makes exactly the same pictures.
"""

import math
import os
import struct

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'knocks')

# materials: what colour a point of the surface is (see colour_of)
SKIN, HOLLOW, TAPE, WOOD, METAL, DARK_METAL, PAPER, CLOTH, RED, BRASS, PLASTIC, YELLOW, ROCK, BONE, DARK_SKIN = range(15)


# ---- shapes: each gives the distance from points p (an array of [x, y, z]) to its surface --------

def length(v):
    return np.sqrt(np.sum(v * v, axis=-1))


def capsule(p, a, b, r):
    """A rod from a to b with round ends, radius r (or r from ra at a to rb at b, as a pair)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    pa, ba = p - a, b - a
    h = np.clip(np.sum(pa * ba, axis=-1) / max(np.dot(ba, ba), 1e-9), 0, 1)
    if isinstance(r, tuple):
        r = r[0] + (r[1] - r[0]) * h
    return length(pa - ba * h[..., None]) - r


def sphere(p, c, r):
    return length(p - np.asarray(c, float)) - r


def ellipsoid(p, c, radii):
    """A squashed sphere: radii across, up and deep (a close estimate of the true distance)."""
    q = (p - np.asarray(c, float)) / np.asarray(radii, float)
    k0 = length(q)
    k1 = length(q / np.asarray(radii, float))
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)


def box(p, c, half, rounding=0.0):
    """A box centred on c, `half` its half sizes, its edges rounded by `rounding`."""
    q = np.abs(p - np.asarray(c, float)) - (np.asarray(half, float) - rounding)
    outside = length(np.maximum(q, 0))
    inside = np.minimum(np.max(q, axis=-1), 0)
    return outside + inside - rounding


def cylinder(p, c, r, half_height, axis=1):
    """An upright cylinder (along y), or along x (axis 0) or z (axis 2)."""
    q = p - np.asarray(c, float)
    others = [i for i in range(3) if i != axis]
    d_round = np.sqrt(q[..., others[0]] ** 2 + q[..., others[1]] ** 2) - r
    d_long = np.abs(q[..., axis]) - half_height
    return np.minimum(np.maximum(d_round, d_long), 0) + length(np.stack([np.maximum(d_round, 0), np.maximum(d_long, 0)], -1))


def smooth_union(a, b, k):
    """Where two shapes meet, blend them into one another over a distance of about k."""
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


class Model:
    """A model being built: its distance so far and, for each point, the material of whichever
    shape is nearest there."""

    def __init__(self, p):
        self.p = p
        self.d = np.full(p.shape[:-1], 1e9)
        self.m = np.zeros(p.shape[:-1], int)

    def add(self, d, material, blend=0.0):
        nearer = d < self.d
        self.m = np.where(nearer, material, self.m)
        self.d = smooth_union(self.d, d, blend) if blend > 0 else np.minimum(self.d, d)

    def cut(self, d, material=None, blend=0.0):
        """Take a shape away. The new surface it leaves is `material`."""
        if blend > 0:
            h = np.clip(0.5 - 0.5 * (self.d + d) / blend, 0, 1)
            new = self.d + (-d - self.d) * h + blend * h * (1 - h)
        else:
            new = np.maximum(self.d, -d)
        if material is not None:
            self.m = np.where(new > self.d + 1e-6, material, self.m)
        self.d = new


# ---- the thing ------------------------------------------------------------------------------------

def rotate_y(v, angle):
    """A point or direction turned round the upright (y) axis."""
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = v
    return np.array([x * c + z * s, y, -x * s + z * c])


def limb(start, length_, pitch, roll=0.0):
    """The far end of a bone `length_` long from `start`, hanging straight down when pitch is 0
    and swung forward (towards +z) by pitch radians, and out sideways by roll."""
    return np.asarray(start, float) + length_ * np.array([math.sin(roll), -math.cos(pitch) * math.cos(roll), math.sin(pitch)])


def beast(p, walk=0.0, tilt=0.0, lunge=0.0):
    """The thing, standing on the floor (y = 0) facing +z. walk is where it is in its stride (0 to
    1, one full step of each leg), tilt how far its head is cocked (radians, for listening),
    lunge from 0 (walking) to 1 (thrown forward at you, arms up, its face open wide).

    It is tall and starved thin, stooped so its head hangs forward under the ceiling: long arms
    with longer fingers hanging past its knees, legs that bend too much, ribs, and where its face
    should be a deep round hollow, ridged like the inside of an ear and ringed with small teeth.
    Its hands and feet are darker, as if burnt. Strips of old brown recording tape hang off its
    shoulders."""
    m = Model(p)
    swing = math.sin(walk * 2 * math.pi)
    bob = 0.015 * abs(math.cos(walk * 2 * math.pi))
    lean = 0.12 + lunge * 0.45                 # how far its body leans forward
    hip = np.array([0, 0.52 + bob - lunge * 0.06, -0.02 - lunge * 0.05])

    # legs: thigh, shin and a long foot. Each swings forward and back with the walk, and the knee
    # bends most as the leg swings through
    for side in (-1, 1):
        phase = swing * side
        thigh_pitch = 0.38 * phase + lunge * 0.5 * (side + 1) / 2
        knee_bend = 0.45 + 0.4 * max(0.0, math.sin(walk * 2 * math.pi + side * math.pi / 2)) + lunge * 0.3
        h = hip + [side * 0.058, 0, 0]
        knee = limb(h, 0.27, thigh_pitch, side * 0.04)
        ankle = limb(knee, 0.28, thigh_pitch - knee_bend, -side * 0.02)
        toe = ankle + [side * 0.01, -0.02, 0.09]
        m.add(capsule(p, h, knee, (0.03, 0.02)), SKIN, 0.02)
        m.add(capsule(p, knee, ankle, (0.019, 0.013)), SKIN, 0.015)
        m.add(capsule(p, ankle, toe, (0.016, 0.01)), DARK_SKIN, 0.012)
        m.add(sphere(p, knee, 0.024), SKIN, 0.01)

    # the body: a narrow pelvis, a waist like a wrist, a ribcage, all leaning forward
    up = np.array([0, math.cos(lean), math.sin(lean)])
    chest = hip + up * 0.27
    m.add(ellipsoid(p, hip, (0.075, 0.045, 0.045)), SKIN, 0.02)
    m.add(capsule(p, hip, chest, 0.03), SKIN, 0.03)
    ribs = ellipsoid(p, hip + up * 0.2, (0.072, 0.105, 0.05))
    # the ribs: the ribcage's surface rippled across, on its upper part only
    rib_height = p[..., 1] - hip[1]
    ribs = ribs + 0.004 * np.sin(rib_height * 150) * (rib_height > 0.17) * (rib_height < 0.33)
    m.add(ribs, SKIN, 0.03)
    # a hunch across its shoulders, the shoulder blades standing out
    for side in (-1, 1):
        m.add(ellipsoid(p, chest + np.array([side * 0.045, -0.03, -0.04]), (0.055, 0.07, 0.04)), SKIN, 0.03)

    # the neck, long and thin, out forward from the top of the chest, and the head on it
    neck_top = chest + np.array([0, 0.03, 0.09 + lunge * 0.06])
    m.add(capsule(p, chest, neck_top, 0.02), SKIN, 0.02)
    # the head hangs a little, its face turned down... until it lunges and looks up at you
    down = 0.15 - lunge * 0.3
    forward = np.array([0, -math.sin(down), math.cos(down)])
    head = neck_top + forward * 0.06 + np.array([math.sin(tilt) * 0.02, 0.02, 0])
    # an egg longer than it is wide, pointing along `forward`: worked out in the head's own
    # directions (across, up and forward) so it can be cocked to one side
    across = np.cross([0, 1, 0], forward)
    across = across / np.linalg.norm(across)
    head_up = np.cross(forward, across)
    c, s = math.cos(tilt), math.sin(tilt)
    across, head_up = across * c + head_up * s, head_up * c - across * s
    local = np.stack([np.sum((p - head) * across, -1), np.sum((p - head) * head_up, -1), np.sum((p - head) * forward, -1)], -1)
    m.add(ellipsoid(local, (0, 0.005, -0.01), (0.062, 0.072, 0.1)), SKIN, 0.02)
    # the hollow: a sphere cut out of its front, with rings in it like the inside of an ear
    hollow_r = 0.055 + lunge * 0.015
    mouth = np.array([0, -0.005, 0.1 + lunge * 0.01])
    rings = 0.003 * np.sin(length(local[..., :2] - mouth[:2]) * 260)
    m.cut(sphere(local, mouth, hollow_r) + rings, HOLLOW, 0.008)
    # and a ring of small teeth just inside its rim, pointing in
    # (uneven: each a different length, and a little crooked)
    for i in range(14):
        a = i / 14 * 2 * math.pi + math.sin(i * 7.3) * 0.12
        rim = np.array([math.cos(a) * 0.043, math.sin(a) * 0.05 - 0.005, 0.062])
        reach = 0.62 + 0.18 * math.sin(i * 3.7)
        tip = rim * [reach, reach, 1.0] + [0, 0, 0.004 * math.sin(i * 5.1)]
        m.add(capsule(local, rim, tip, (0.0055, 0.0012)), BONE)

    # arms: from shoulders out at the top of the chest, hanging long, swinging against the legs.
    # Lunging, they come up and forward, reaching for you
    for side in (-1, 1):
        shoulder = chest + np.array([side * 0.1, -0.01, -0.01])
        m.add(capsule(p, chest, shoulder, 0.022), SKIN, 0.02)
        pitch = -0.18 * swing * side * (1 - lunge) + lunge * 1.35
        elbow = limb(shoulder, 0.27, pitch + 0.1, side * (0.14 + lunge * 0.05))
        wrist = limb(elbow, 0.27, pitch + 0.3 + lunge * 0.1, side * (0.02 - lunge * 0.12))
        m.add(capsule(p, shoulder, elbow, (0.022, 0.016)), SKIN, 0.015)
        m.add(capsule(p, elbow, wrist, (0.016, 0.012)), SKIN, 0.012)
        m.add(sphere(p, elbow, 0.018), SKIN, 0.01)
        # four long fingers, spread a little, and curled at the tips
        hand_dir = (wrist - elbow) / np.linalg.norm(wrist - elbow)
        for f in range(4):
            spread = (f - 1.5) * 0.18
            d1 = rotate_y(hand_dir, spread * side) + np.array([side * spread * 0.2, 0, 0])
            d1 = d1 / np.linalg.norm(d1)
            knuckle = wrist + d1 * 0.085
            tip = knuckle + (d1 + np.array([0, 0, 0.6 + lunge * 0.2])) / 1.4 * 0.085
            m.add(capsule(p, wrist, knuckle, 0.0085), DARK_SKIN, 0.006)
            m.add(capsule(p, knuckle, tip, (0.007, 0.003)), DARK_SKIN, 0.004)

        # strips of tape hanging from the shoulder, swaying a little behind the walk
        for strip in range(3):
            top = shoulder + np.array([side * 0.01 * strip, 0.0, -0.02 + strip * 0.02])
            sway = 0.04 * math.sin(walk * 2 * math.pi + strip + side)
            bottom = top + np.array([side * 0.02 + sway, -0.3 - strip * 0.08, -0.03 - lunge * 0.1])
            m.add(capsule(p, top, bottom, 0.0055), TAPE)
    return m


# ---- props ----------------------------------------------------------------------------------------

def prop(p, kind):
    """One of the things in the observatory, in a square one unit across (-0.5 to 0.5), on the floor."""
    m = Model(p)
    if kind == 'desk':
        m.add(box(p, (0, 0.38, 0), (0.42, 0.025, 0.25), 0.005), WOOD)
        for x in (-0.38, 0.38):
            m.add(box(p, (x, 0.18, 0), (0.025, 0.18, 0.22)), WOOD)
        m.add(box(p, (0.24, 0.22, 0.02), (0.13, 0.14, 0.22)), WOOD)
        m.add(box(p, (0.24, 0.27, 0.24), (0.11, 0.05, 0.005)), DARK_METAL)
        m.add(box(p, (-0.12, 0.41, 0.05), (0.1, 0.005, 0.07)), PAPER)
        m.add(box(p, (0.05, 0.415, -0.05), (0.08, 0.008, 0.1)), PAPER)
        m.add(cylinder(p, (0.3, 0.42, -0.1), 0.03, 0.02), METAL)
        m.add(capsule(p, (0.3, 0.42, -0.1), (0.25, 0.6, -0.05), 0.008), METAL)
        m.add(ellipsoid(p, (0.22, 0.6, 0.0), (0.06, 0.04, 0.05)), DARK_METAL)
    elif kind == 'chair':
        m.add(box(p, (0, 0.24, 0), (0.17, 0.02, 0.17), 0.005), WOOD)
        m.add(box(p, (0, 0.5, -0.16), (0.16, 0.12, 0.015), 0.005), WOOD)
        for x in (-0.15, 0.15):
            for z in (-0.15, 0.15):
                m.add(capsule(p, (x, 0, z), (x, 0.24, z), 0.012), WOOD)
            m.add(capsule(p, (x, 0.24, -0.16), (x, 0.62, -0.16), 0.013), WOOD)
    elif kind == 'cabinet':
        m.add(box(p, (0, 0.38, 0), (0.2, 0.38, 0.22), 0.01), METAL)
        for i in range(4):
            y = 0.1 + i * 0.185
            m.add(box(p, (0, y, 0.225), (0.18, 0.08, 0.01), 0.004), METAL)
            m.add(box(p, (0, y + 0.03, 0.24), (0.05, 0.01, 0.012)), BRASS)
        m.add(box(p, (0, 0.53, 0.32), (0.17, 0.008, 0.12)), METAL)   # a drawer left open
    elif kind == 'recorder':
        m.add(box(p, (0, 0.2, 0), (0.25, 0.2, 0.2), 0.01), WOOD)
        m.add(box(p, (0, 0.47, 0), (0.22, 0.07, 0.14), 0.01), PLASTIC)
        for x in (-0.11, 0.11):
            m.add(cylinder(p, (x, 0.6, 0.02), 0.085, 0.006, axis=2), DARK_METAL)
            m.add(cylinder(p, (x, 0.6, 0.03), 0.02, 0.01, axis=2), METAL)
        for x in range(4):
            m.add(box(p, (-0.12 + x * 0.08, 0.44, 0.145), (0.025, 0.012, 0.01)), METAL)
        m.add(sphere(p, (0.17, 0.5, 0.14), 0.012), RED)
    elif kind == 'phone':
        m.add(box(p, (0, 0.28, 0), (0.18, 0.02, 0.18)), WOOD)
        m.add(capsule(p, (0, 0, 0), (0, 0.28, 0), 0.025), WOOD)
        m.add(box(p, (0, 0.34, 0), (0.1, 0.04, 0.09), 0.02), PLASTIC)
        m.add(capsule(p, (-0.09, 0.4, 0), (0.09, 0.4, 0), 0.022), PLASTIC)
        m.add(cylinder(p, (0, 0.355, 0.07), 0.04, 0.012, axis=2), METAL)
    elif kind == 'coatstand':
        m.add(capsule(p, (0, 0, 0), (0, 0.86, 0), 0.013), WOOD)
        for a in range(3):
            ang = a * 2.1
            m.add(capsule(p, (0, 0.02, 0), (math.cos(ang) * 0.16, 0, math.sin(ang) * 0.16), 0.012), WOOD)
        # the long coat, shoulders and all: from far off, in a flash, it is a man standing there
        coat = capsule(p, (0, 0.78, 0), (0, 0.18, 0.02), (0.1, 0.13))
        coat = smooth_union(coat, capsule(p, (-0.11, 0.76, 0), (0.11, 0.76, 0), 0.04), 0.05)
        m.add(coat, CLOTH)
        for side in (-1, 1):
            m.add(capsule(p, (side * 0.13, 0.75, 0), (side * 0.14, 0.42, 0.02), 0.035), CLOTH, 0.02)
        m.add(cylinder(p, (0, 0.9, 0), 0.075, 0.035), CLOTH)       # a hat
        m.add(cylinder(p, (0, 0.865, 0), 0.12, 0.006), CLOTH)
    elif kind == 'lockers':
        for i, x in enumerate((-0.27, 0, 0.27)):
            m.add(box(p, (x, 0.45, 0), (0.13, 0.45, 0.2), 0.004), METAL if i != 1 else DARK_METAL)
            for y in (0.78, 0.82):
                m.add(box(p, (x, y, 0.205), (0.08, 0.008, 0.004)), DARK_METAL)
        m.add(box(p, (0.0, 0.45, 0.24), (0.005, 0.42, 0.13)), DARK_METAL)   # a door hanging open
    elif kind == 'bed':
        for y in (0.2, 0.62):
            m.add(box(p, (0, y, 0), (0.45, 0.04, 0.2), 0.01), METAL)
            m.add(box(p, (0, y + 0.07, 0), (0.43, 0.04, 0.19), 0.03), CLOTH)
        for x in (-0.44, 0.44):
            m.add(capsule(p, (x, 0, 0.18), (x, 0.75, 0.18), 0.015), METAL)
    elif kind == 'note':
        m.add(box(p, (0, 0.17, 0.05), (0.11, 0.15, 0.008), 0.003), PAPER)
        m.add(box(p, (0, 0.33, 0.05), (0.04, 0.012, 0.015)), METAL)
        m.add(box(p, (0, 0.03, -0.02), (0.13, 0.03, 0.06)), WOOD)
    elif kind == 'battery':
        m.add(cylinder(p, (0, 0.09, 0), 0.04, 0.08), YELLOW)
        m.add(cylinder(p, (0, 0.05, 0), 0.041, 0.04), DARK_METAL)
        m.add(cylinder(p, (0, 0.18, 0), 0.012, 0.012), METAL)
        m.add(cylinder(p, (0.12, 0.09, -0.05), 0.04, 0.08), YELLOW)
        m.add(cylinder(p, (0.12, 0.05, -0.05), 0.041, 0.04), DARK_METAL)
    elif kind == 'fuse':
        m.add(cylinder(p, (0, 0.06, 0), 0.05, 0.11, axis=0), PAPER)
        for x in (-0.12, 0.12):
            m.add(cylinder(p, (x, 0.06, 0), 0.055, 0.02, axis=0), BRASS)
        m.add(box(p, (0, 0.06, 0.05), (0.05, 0.02, 0.003)), RED)
    elif kind == 'tape':
        m.add(cylinder(p, (0, 0.13, 0), 0.13, 0.008, axis=2), PLASTIC)
        m.add(cylinder(p, (0, 0.13, 0.009), 0.085, 0.006, axis=2), TAPE)
        m.add(cylinder(p, (0, 0.13, 0.016), 0.025, 0.006, axis=2), METAL)
        m.add(box(p, (0, 0.01, 0), (0.1, 0.01, 0.04)), WOOD)
    elif kind == 'key':
        m.add(box(p, (0, 0.02, 0), (0.12, 0.02, 0.08)), WOOD)
        m.add(cylinder(p, (-0.05, 0.12, 0), 0.04, 0.006, axis=2), BRASS)
        m.add(box(p, (0.04, 0.12, 0), (0.07, 0.01, 0.006)), BRASS)
        m.add(box(p, (0.09, 0.1, 0), (0.008, 0.02, 0.006)), BRASS)
        m.add(box(p, (-0.05, 0.12, 0.0), (0.01, 0.01, 0.01)), DARK_METAL)
        m.add(box(p, (-0.1, 0.2, 0), (0.05, 0.035, 0.003)), RED)        # its tag
    elif kind == 'speaker':
        m.add(capsule(p, (0, 0, 0), (0, 0.7, 0), 0.018), METAL)
        horn = cylinder(p, (0, 0.78, 0.05), 0.11, 0.07, axis=2)
        horn = np.maximum(horn, -cylinder(p, (0, 0.78, 0.1), 0.09, 0.07, axis=2))
        m.add(horn, METAL)
        m.add(cylinder(p, (0, 0.78, -0.03), 0.05, 0.03, axis=2), DARK_METAL)
    elif kind == 'crates':
        m.add(box(p, (-0.15, 0.17, 0), (0.22, 0.17, 0.2), 0.01), WOOD)
        m.add(box(p, (0.2, 0.13, 0.05), (0.16, 0.13, 0.16), 0.01), WOOD)
        m.add(box(p, (-0.1, 0.45, -0.02), (0.15, 0.11, 0.15), 0.01), WOOD)
    elif kind == 'belongings':
        # a coat folded on the ground, headphones on it, and the recorder beside it
        m.add(box(p, (-0.08, 0.04, 0), (0.24, 0.04, 0.16), 0.035), CLOTH)
        band = np.abs(length((p - [-0.05, 0.1, 0.02])[..., :2] * [1, 1]) - 0.07) - 0.008
        band = np.maximum(band, np.abs(p[..., 2] - 0.02) - 0.01)
        band = np.maximum(band, 0.1 - p[..., 1] - 0.0)
        m.add(band, DARK_METAL)
        for x in (-0.12, 0.02):
            m.add(cylinder(p, (x, 0.1, 0.02), 0.03, 0.015, axis=0), DARK_METAL)
        m.add(box(p, (0.25, 0.06, 0.05), (0.09, 0.06, 0.06), 0.01), PLASTIC)
    elif kind == 'rubble':
        rng = np.random.default_rng(5)
        for _ in range(9):
            c = (rng.uniform(-0.35, 0.35), rng.uniform(0.0, 0.1), rng.uniform(-0.3, 0.3))
            m.add(ellipsoid(p, c, (rng.uniform(0.05, 0.14), rng.uniform(0.04, 0.1), rng.uniform(0.05, 0.12))), ROCK, 0.02)
    elif kind == 'drum':
        m.add(cylinder(p, (0, 0.3, 0), 0.17, 0.3), RED)
        for y in (0.12, 0.48):
            m.add(cylinder(p, (0, y, 0), 0.178, 0.012), DARK_METAL)
    return m


PROPS = ['desk', 'chair', 'cabinet', 'recorder', 'phone', 'coatstand', 'lockers', 'bed', 'note',
         'battery', 'fuse', 'tape', 'key', 'speaker', 'crates', 'belongings', 'rubble', 'drum']


# ---- colours and light ----------------------------------------------------------------------------

def hash3(p, scale):
    """Smooth-ish blotches from 0 to 1 at points p: sines of the position at odd angles, mixed.
    Cheap stand-in for 3D noise, enough for mottled skin and grain."""
    q = p * scale
    v = np.sin(q[..., 0] * 1.7 + np.sin(q[..., 1] * 2.3)) + np.sin(q[..., 1] * 1.9 + np.sin(q[..., 2] * 2.9)) \
        + np.sin(q[..., 2] * 2.1 + np.sin(q[..., 0] * 3.1))
    return v / 6 + 0.5


def colour_of(material, p):
    """The colour of each point (an array of [r, g, b], 0 to 1) from its material and where it is."""
    blotch = hash3(p, 40)
    fine = hash3(p, 160)
    base = {
        SKIN: (0.5, 0.47, 0.41), HOLLOW: (0.14, 0.03, 0.04), TAPE: (0.17, 0.11, 0.07),
        BONE: (0.55, 0.47, 0.32), DARK_SKIN: (0.2, 0.16, 0.14),
        WOOD: (0.42, 0.27, 0.15), METAL: (0.48, 0.5, 0.5), DARK_METAL: (0.18, 0.19, 0.2),
        PAPER: (0.86, 0.84, 0.76), CLOTH: (0.24, 0.22, 0.2), RED: (0.6, 0.1, 0.08),
        BRASS: (0.7, 0.55, 0.25), PLASTIC: (0.3, 0.28, 0.24), YELLOW: (0.75, 0.6, 0.12),
        ROCK: (0.36, 0.31, 0.27)}
    colour = np.zeros(p.shape)
    for mat, rgb in base.items():
        colour[material == mat] = rgb
    # skin: blotched grey-yellow, darker in the blotches, veined faintly
    skin = material == SKIN
    colour[skin] *= (0.5 + 0.6 * blotch[skin] * (0.7 + 0.3 * fine[skin]))[:, None]
    colour[skin] -= (np.clip(fine[skin] - 0.82, 0, 1) * 1.5)[:, None] * np.array([0.1, 0.2, 0.15])
    # everything else gets a little grain
    other = ~skin
    colour[other] *= (0.85 + 0.25 * fine[other])[:, None]
    return np.clip(colour, 0, 1)


# ---- the renderer ---------------------------------------------------------------------------------

def render(scene, width, height, units_wide, units_tall, eye_height=None, pitch=0.0, angle=0.0,
           supersample=2, steps=110, perspective=None):
    """Render scene(points) -> Model into an RGBA picture (numpy, width x height, 0 to 255).

    The camera looks along -z at the model from in front, turned round it by `angle` (radians:
    pi / 2 shows its left side), tilted down by `pitch`. Without `perspective` it is
    "orthographic": every ray parallel, so a sprite's size depends only on how far away the game
    draws it. The picture covers units_wide x units_tall of the scene, its bottom on the floor
    (or, with perspective, the field of view is set by perspective = (eye, look_at, fov))."""
    w, h = width * supersample, height * supersample
    ys, xs = np.mgrid[0:h, 0:w].astype(float)
    if perspective is None:
        u = ((xs + 0.5) / w - 0.5) * units_wide
        v = (1 - (ys + 0.5) / h) * units_tall
        origin = np.stack([u, v, np.full_like(u, 3.0)], -1)
        direction = np.broadcast_to(np.array([0, 0, -1.0]), origin.shape).copy()
        # tilt the camera down: turn origins and directions round the x axis about the middle
        c, s = math.cos(pitch), math.sin(pitch)
        mid = units_tall / 2
        oy, oz = origin[..., 1] - mid, origin[..., 2]
        origin[..., 1], origin[..., 2] = oy * c + oz * s + mid, -oy * s + oz * c
        direction[..., 1], direction[..., 2] = direction[..., 2] * s * 1.0, direction[..., 2] * c
    else:
        eye, target, fov = perspective
        eye, target = np.array(eye, float), np.array(target, float)
        fwd = (target - eye) / np.linalg.norm(target - eye)
        right = np.cross(fwd, [0, 1, 0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        half = math.tan(fov / 2)
        u = ((xs + 0.5) / w * 2 - 1) * half * w / h
        v = (1 - (ys + 0.5) / h * 2) * half
        direction = fwd + u[..., None] * right + v[..., None] * up
        direction /= length(direction)[..., None]
        origin = np.broadcast_to(eye, direction.shape).copy()
    # turning the camera round the model is the same as turning the model the other way: turn
    # the rays instead, round the upright axis through the model's middle
    c, s = math.cos(angle), math.sin(angle)

    def turn(v):
        out = v.copy()
        out[..., 0], out[..., 2] = v[..., 0] * c - v[..., 2] * s, v[..., 0] * s + v[..., 2] * c
        return out
    origin, direction = turn(origin), turn(direction)

    flat_o, flat_d = origin.reshape(-1, 3), direction.reshape(-1, 3)
    t = np.zeros(len(flat_o))
    hit = np.zeros(len(flat_o), bool)
    alive = np.ones(len(flat_o), bool)
    for _ in range(steps):
        idx = np.nonzero(alive)[0]
        if len(idx) == 0:
            break
        d = scene(flat_o[idx] + flat_d[idx] * t[idx, None]).d
        t[idx] += d * 0.8          # a little under the full distance: the shapes' sums are estimates
        close = d < 0.0008
        hit[idx[close]] = True
        alive[idx[close]] = False
        alive[idx[t[idx] > 6]] = False

    rgba = np.zeros((len(flat_o), 4))
    idx = np.nonzero(hit)[0]
    if len(idx):
        pts = flat_o[idx] + flat_d[idx] * t[idx, None]
        model = scene(pts)
        # the normal: which way the distance grows fastest
        e = 0.0015
        n = np.stack([scene(pts + [e, 0, 0]).d - scene(pts - [e, 0, 0]).d,
                      scene(pts + [0, e, 0]).d - scene(pts - [0, e, 0]).d,
                      scene(pts + [0, 0, e]).d - scene(pts - [0, 0, e]).d], -1)
        n /= np.maximum(length(n), 1e-9)[:, None]
        to_eye = -flat_d[idx]
        # the light: a lamp at the viewer (the flashlight), a faint light from above, and shine
        lamp = np.clip(np.sum(n * to_eye, -1), 0, 1)
        above = np.clip(n[:, 1], 0, 1) * 0.15
        shine = np.clip(np.sum(n * to_eye, -1), 0, 1) ** 12 * 0.22
        # "ambient occlusion": a point with the surface close round it (a crease, the inside of
        # the hollow) gets less light. Step out along the normal and see how far the surface is
        occlusion = np.ones(len(idx))
        for k, step in enumerate((0.01, 0.025, 0.05)):
            occlusion -= np.clip(step - scene(pts + n * step).d, 0, 1) * (3.0 / (k + 1))
        occlusion = np.clip(occlusion, 0.15, 1)
        colour = colour_of(model.m, pts)
        wet = np.isin(model.m, [SKIN, DARK_SKIN, HOLLOW, TAPE, BONE, METAL, BRASS, PLASTIC])
        lit = colour * ((0.12 + lamp * 0.95 + above) * occlusion)[:, None] + (shine * wet)[:, None]
        rgba[idx, :3] = np.clip(lit, 0, 1) * 255
        rgba[idx, 3] = 255
    rgba = rgba.reshape(h, w, 4)
    # shrink back to the asked size, averaging each block of pixels: colours weighted by how
    # solid they are, so edges blend into the see-through rather than into black
    blocks = rgba.reshape(height, supersample, width, supersample, 4)
    alpha = blocks[..., 3].mean(axis=(1, 3))
    colour = (blocks[..., :3] * blocks[..., 3:4]).sum(axis=(1, 3)) / np.maximum(blocks[..., 3:4].sum(axis=(1, 3)), 1e-9)
    return np.concatenate([colour, alpha[..., None]], -1)


def save(name, frames, solid=False):
    """Write frames (RGBA arrays, all the same height) side by side as one 32 bit BMP, its 4th
    byte the alpha. A pixel at least half solid is kept (255), any less is see-through (0): AerOS
    draws a pixel or leaves it, nothing between. With solid, every pixel is kept."""
    pic = np.concatenate(frames, axis=1)
    pic = np.clip(np.round(pic), 0, 255).astype(np.uint8)
    if solid:
        pic[..., 3] = 255
    else:
        pic[..., 3] = np.where(pic[..., 3] >= 128, 255, 0)
        pic[pic[..., 3] == 0, :3] = 0
    h, w = pic.shape[:2]
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        rows += pic[y][:, [2, 1, 0, 3]].tobytes()
    header = struct.pack('<2sIHHI', b'BM', 54 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 32, 0, len(rows), 2835, 2835, 0, 0)
    with open(os.path.join(OUT, name + '.bmp'), 'wb') as f:
        f.write(header + info + rows)
    return pic


BEAST_W, BEAST_H = 72, 96      # a frame of the thing: 0.75 x 1 square
WALK_FRAMES = 8
ANGLES = 5                     # 0, 45, 90, 135 and 180 degrees round from in front


def main():
    os.makedirs(OUT, exist_ok=True)

    # the thing: every angle's walk, then every angle's listening
    frames = []
    for a in range(ANGLES):
        for f in range(WALK_FRAMES):
            frames.append(render(lambda p: beast(p, walk=f / WALK_FRAMES), BEAST_W, BEAST_H, 0.75, 1.0,
                                 angle=a * math.pi / 4))
    for a in range(ANGLES):
        for tilt in (0.45, -0.3):
            frames.append(render(lambda p: beast(p, walk=0.0, tilt=tilt), BEAST_W, BEAST_H, 0.75, 1.0,
                                 angle=a * math.pi / 4))
    save('beast', frames)
    print(f'beast.bmp: {len(frames)} frames')

    lunges = [render(lambda p: beast(p, walk=0.25, lunge=k / 3), BEAST_W, BEAST_H, 0.75, 1.0) for k in range(4)]
    save('lunge', lunges)

    props = [render(lambda p: prop(p, kind), 64, 64, 1.0, 1.0, pitch=0.18) for kind in PROPS]
    save('props', props)
    print(f'props.bmp: {len(props)} things')

    # the close-up: its face right in yours, its hands coming in from the sides
    scare = render(lambda p: beast(p, walk=0.25, lunge=1.0), 320, 240, 1, 1,
                   perspective=((0.0, 0.72, 0.58), (0.0, 0.75, 0.28), 1.15), supersample=2, steps=140)
    save('scare', [scare], solid=True)
    print('lunge.bmp, scare.bmp')


if __name__ == '__main__':
    main()
