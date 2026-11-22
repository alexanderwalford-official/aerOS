#!/usr/bin/env python3
"""
make_maze3d_sounds.py - make Maze 3D's sound effects and music, in the style of old game chips

    python3 tools/make_maze3d_sounds.py

writes them to packages/maze3d/, where pkg install puts them in /apps/maze3d/ next to the game
(packages/maze3d.aer, which loads them with sload). Nothing is recorded or sampled: every sound
is made here from a few plain waves, the way a 1980s sound chip made them:

    pulse      a square wave, on for part of each cycle ("duty"): the chip's main voice
    triangle   softer, for bass lines and bells
    sine       for bells and the kick drum
    noise      random values, for drums, footsteps and puffs of smoke

Waves with sharp corners (pulse, triangle) are built from their harmonics, stopping below half
the sample rate, so high notes don't turn into whistles at these low rates ("aliasing").

The files are 8 bit mono WAVs, to keep the package small: the effects at 16000 samples a
second, the music at 11025. AerOS turns them into 16 bit stereo at 48000 when it loads them.
The music is written as loops: anything still ringing at the end is wrapped round to the
start, so they loop without a click. Running this again makes exactly the same files (the
noise comes from a fixed seed).
"""

import math
import os
import random
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'maze3d')

EFFECT_RATE = 16000
MUSIC_RATE = 11025

noise = random.Random(3).uniform   # the same "random" noise every time


def note(name):
    """A note's frequency in Hz, from its name: 'A4' is 440, 'C#5', 'Bb3'..."""
    steps = {'C': -9, 'D': -7, 'E': -5, 'F': -4, 'G': -2, 'A': 0, 'B': 2}[name[0]]
    rest = name[1:]
    if rest.startswith('#'):
        steps, rest = steps + 1, rest[1:]
    elif rest.startswith('b'):
        steps, rest = steps - 1, rest[1:]
    return 440.0 * 2 ** ((steps + 12 * (int(rest) - 4)) / 12)


# ---- one cycle of each wave, kept in a table ----------------------------------------------------

TABLE = 1024           # samples in one cycle of a table
tables = {}


def table(shape, hz, rate, duty=0.5):
    """One cycle of a wave as a list, with only the harmonics that fit below half the rate."""
    harmonics = max(1, int(rate / 2 / hz))
    key = (shape, harmonics, duty)
    if key not in tables:
        cycle = [0.0] * TABLE
        for k in range(1, min(harmonics, 40) + 1):
            if shape == 'pulse':
                # a pulse wave on for `duty` of each cycle is this sum of cosines
                amount = 2 / (k * math.pi) * math.sin(k * math.pi * duty)
                phase = 0.0
            elif k % 2 == 1:
                # a triangle has only odd harmonics, each 1 / k squared, every other one upside down
                amount = 8 / math.pi ** 2 / k ** 2 * (-1) ** ((k - 1) // 2)
                phase = -math.pi / 2
            else:
                continue
            for i in range(TABLE):
                cycle[i] += amount * math.cos(2 * math.pi * k * i / TABLE + phase)
        tables[key] = cycle
    return tables[key]


def tone(shape, seconds, rate, hz, volume=1.0, duty=0.5, attack=0.005, decay=None,
         vibrato=0.0, slide_to=None):
    """A note: `shape` is 'pulse', 'triangle' or 'sine'. `hz` can glide to `slide_to` over the
    note. `decay`, if given, is how many seconds it takes to fade to about a third (otherwise it
    holds, and only fades out over its last 10 ms). `vibrato` wobbles the pitch by that fraction."""
    count = int(seconds * rate)
    out = [0.0] * count
    phase = 0.0
    cycle = None if shape == 'sine' else table(shape, max(hz, slide_to or hz), rate, duty)
    for i in range(count):
        t = i / rate
        f = hz if slide_to is None else hz * (slide_to / hz) ** (i / count)
        if vibrato:
            f *= 1 + vibrato * math.sin(2 * math.pi * 5.5 * t)
        phase = (phase + f / rate) % 1.0
        if cycle is None:
            value = math.sin(2 * math.pi * phase)
        else:
            value = cycle[int(phase * TABLE)]
        level = min(1.0, t / attack) if attack else 1.0
        if decay:
            level *= math.exp(-t / decay)
        level *= min(1.0, (seconds - t) / 0.01)
        out[i] = value * level * volume
    return out


def hiss(seconds, rate, volume=1.0, decay=0.05, smooth=0.0):
    """Noise that fades away: `smooth` (0 to nearly 1) takes off the high part, making a thump
    instead of a hiss."""
    out = []
    last = 0.0
    for i in range(int(seconds * rate)):
        last = last * smooth + noise(-1, 1) * (1 - smooth)
        out.append(last * volume * math.exp(-i / rate / decay))
    return out


# ---- putting sounds together, and writing them ------------------------------------------------

class Sound:
    """A sound being put together: notes are added in at their times, and add up."""

    def __init__(self, seconds, rate, loop=False):
        self.rate = rate
        self.loop = loop
        self.samples = [0.0] * int(seconds * rate)

    def add(self, at, part):
        """Mix `part` (a list of samples) in, starting `at` seconds. In a loop, anything past the
        end goes round to the start."""
        start = int(at * self.rate)
        size = len(self.samples)
        for i, value in enumerate(part):
            j = start + i
            if self.loop:
                j %= size
            elif j >= size:
                break
            self.samples[j] += value

    def write(self, name):
        """Write it as an 8 bit mono WAV, as loud as it can be without clipping. The name must be
        8 letters at most: a package's files need 8.3 names (see docs/PACKAGES.md)."""
        assert len(name) <= 8 and name == name.lower(), f'{name}.wav is not an 8.3 name'
        peak = max(1e-9, max(abs(s) for s in self.samples))
        scale = 0.95 / peak
        data = bytes(max(0, min(255, int(round(128 + s * scale * 127)))) for s in self.samples)
        path = os.path.join(OUT, name + '.wav')
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(1)        # 8 bit WAVs are unsigned: 128 is silence
            w.setframerate(self.rate)
            w.writeframes(data)
        print(f'{path}  {os.path.getsize(path) // 1024} KB')


def effect(seconds):
    return Sound(seconds, EFFECT_RATE)


R = EFFECT_RATE

# ---- the sound effects ----------------------------------------------------------------------------


def crystal():
    """Picking up a crystal: two quick notes, then a bell on C7 (2093 Hz) that rings away. (The
    game's test listens for that C7)"""
    s = effect(1.1)
    s.add(0.00, tone('pulse', 0.06, R, note('E6'), 0.35, duty=0.25))
    s.add(0.06, tone('pulse', 0.06, R, note('G6'), 0.35, duty=0.25))
    s.add(0.12, tone('sine', 0.95, R, note('C7'), 0.9, decay=0.45))
    s.write('crystal')


def door():
    """The last crystal: the door opens. A rising chord on a bell-like triangle."""
    s = effect(1.2)
    for i, name in enumerate(['C5', 'E5', 'G5', 'C6', 'E6']):
        s.add(i * 0.09, tone('triangle', 1.2 - i * 0.09, R, note(name), 0.5, decay=0.35))
    s.add(0.45, tone('pulse', 0.6, R, note('G6'), 0.15, duty=0.125, decay=0.2, vibrato=0.01))
    s.write('door')


def light():
    """Shining the light: a bright sweep upwards, with a sparkle of noise."""
    s = effect(0.3)
    s.add(0.0, tone('sine', 0.25, R, 500, 0.7, decay=0.1, slide_to=2400))
    s.add(0.0, tone('pulse', 0.2, R, 1000, 0.15, duty=0.125, decay=0.06, slide_to=3000))
    s.add(0.02, hiss(0.2, R, 0.15, decay=0.05))
    s.write('light')


def banish():
    """A ghost sent away: a wobbling wail that falls away, and a puff."""
    s = effect(0.8)
    s.add(0.0, tone('triangle', 0.75, R, 900, 0.8, decay=0.35, vibrato=0.06, slide_to=180))
    s.add(0.0, hiss(0.5, R, 0.4, decay=0.15, smooth=0.6))
    s.write('banish')


def hurt():
    """A ghost touches you: a harsh buzz that drops, over a crunch."""
    s = effect(0.5)
    s.add(0.0, tone('pulse', 0.45, R, 320, 0.7, duty=0.5, decay=0.25, slide_to=70))
    s.add(0.0, tone('pulse', 0.45, R, 330, 0.4, duty=0.25, decay=0.25, slide_to=75))
    s.add(0.0, hiss(0.3, R, 0.6, decay=0.08, smooth=0.3))
    s.write('hurt')


def step():
    """A footstep: a short soft thump."""
    s = effect(0.08)
    s.add(0.0, hiss(0.08, R, 1.0, decay=0.018, smooth=0.9))
    s.add(0.0, tone('sine', 0.06, R, 110, 0.4, decay=0.02, slide_to=70))
    s.write('step')


def bump():
    """Walking into a wall: a dull thud."""
    s = effect(0.16)
    s.add(0.0, tone('sine', 0.15, R, 95, 1.0, decay=0.05, slide_to=45))
    s.add(0.0, hiss(0.1, R, 0.5, decay=0.03, smooth=0.85))
    s.write('bump')


def spotted():
    """A ghost has seen you and starts to chase: a low, uneasy pair of notes that beat
    against each other."""
    s = effect(0.9)
    for hz in (note('A3'), note('A3') * 1.03):
        part = tone('triangle', 0.9, R, hz, 0.5, attack=0.25, vibrato=0.015)
        part = [v * math.sin(math.pi * i / len(part)) for i, v in enumerate(part)]
        s.add(0.0, part)
    s.add(0.0, tone('sine', 0.9, R, note('E5'), 0.12, attack=0.3, vibrato=0.03))
    s.write('spotted')


def won():
    """Out of the maze: a short fanfare."""
    s = effect(1.6)
    for i, name in enumerate(['C5', 'E5', 'G5', 'C6']):
        s.add(i * 0.12, tone('pulse', 0.12, R, note(name), 0.5, duty=0.25))
    for name in ('E5', 'G5', 'C6'):
        s.add(0.5, tone('pulse', 1.0, R, note(name), 0.3, duty=0.5, decay=0.5, vibrato=0.008))
    s.add(0.5, tone('triangle', 1.0, R, note('C4'), 0.6, decay=0.5))
    s.write('won')


def caught():
    """Caught: a slow fall down, ending on a wobbling low note."""
    s = effect(1.7)
    for i, name in enumerate(['G4', 'F#4', 'F4']):
        s.add(i * 0.25, tone('pulse', 0.24, R, note(name), 0.5, duty=0.5))
    s.add(0.75, tone('pulse', 0.9, R, note('E4'), 0.5, duty=0.5, decay=0.5, vibrato=0.03))
    s.add(0.75, tone('triangle', 0.9, R, note('E2'), 0.7, decay=0.5))
    s.write('caught')


def menu_move():
    """Moving between the menu's lines: a tiny blip."""
    s = effect(0.05)
    s.add(0.0, tone('pulse', 0.04, R, note('A5'), 0.6, duty=0.25, decay=0.02))
    s.write('menumove')


def menu_change():
    """Changing a choice on the menu: two blips, up."""
    s = effect(0.1)
    s.add(0.0, tone('pulse', 0.04, R, note('E5'), 0.6, duty=0.25, decay=0.03))
    s.add(0.045, tone('pulse', 0.05, R, note('B5'), 0.6, duty=0.25, decay=0.03))
    s.write('menuchg')


def start():
    """Starting a game: a quick run upwards."""
    s = effect(0.4)
    for i, name in enumerate(['A4', 'C5', 'E5', 'A5']):
        s.add(i * 0.06, tone('pulse', 0.1, R, note(name), 0.5, duty=0.25, decay=0.06))
    s.add(0.24, tone('triangle', 0.16, R, note('A3'), 0.6, decay=0.08))
    s.write('start')


# ---- the music ------------------------------------------------------------------------------------

M = MUSIC_RATE
BEAT = 0.5             # 120 beats a minute
EIGHTH = BEAT / 2
SIXTEENTH = BEAT / 4
BAR = BEAT * 4

# The chords, one a bar, as the notes in them (low to high)
CHORDS = {
    'Am': ['A', 'C', 'E'], 'F': ['F', 'A', 'C'], 'G': ['G', 'B', 'D'], 'Em': ['E', 'G', 'B'],
    'Dm': ['D', 'F', 'A'], 'E': ['E', 'G#', 'B'],
}


def kick():
    return tone('sine', 0.15, M, 130, 1.0, decay=0.05, slide_to=40)


def snare():
    return hiss(0.12, M, 0.35, decay=0.04, smooth=0.2)


def hat():
    return hiss(0.03, M, 0.12, decay=0.01)


def game_music():
    """While you play: 8 bars in A minor, 16 seconds round. A bass line, a quiet arpeggio of
    each chord, drums, and a tune over the second half."""
    bars = ['Am', 'F', 'G', 'Em', 'Am', 'F', 'Dm', 'E']
    s = Sound(BAR * len(bars), M, loop=True)
    for b, chord in enumerate(bars):
        start = b * BAR
        root = CHORDS[chord][0]
        # bass: the chord's root, in a pattern that skips a beat to push forwards
        for eighth, octave in [(0, 2), (2, 2), (3, 3), (4, 2), (6, 2), (7, 3)]:
            s.add(start + eighth * EIGHTH, tone('triangle', EIGHTH * 0.9, M,
                                               note(root + str(octave)), 0.9, decay=0.2))
        # arpeggio: the chord's notes going up and down, one each sixteenth, quietly
        tones = [n + '4' for n in CHORDS[chord]] + [CHORDS[chord][0] + '5']
        order = [0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2, 1]
        for i, k in enumerate(order):
            s.add(start + i * SIXTEENTH, tone('pulse', SIXTEENTH, M, note(tones[k]), 0.16,
                                              duty=0.125, decay=0.06))
        # drums: kick on beats 1 and 3, snare on 2 and 4, a hat between every beat
        for beat in range(4):
            s.add(start + beat * BEAT, kick() if beat % 2 == 0 else snare())
            s.add(start + beat * BEAT + EIGHTH, hat())

    # the tune over bars 5 to 8, as (note, eighths long), None for a rest
    tune = [('E5', 3), ('D5', 1), ('C5', 2), ('B4', 1), ('C5', 1),
            ('A4', 4), ('C5', 2), ('D5', 2),
            ('F5', 2), ('E5', 2), ('D5', 2), ('C5', 1), ('D5', 1),
            ('B4', 4), ('G#4', 3), (None, 1)]
    at = 4 * BAR
    for name, length in tune:
        if name:
            s.add(at, tone('pulse', length * EIGHTH * 0.95, M, note(name), 0.3, duty=0.25,
                           vibrato=0.006))
        at += length * EIGHTH
    s.write('gamemus')


def menu_music():
    """On the menu: 4 slow bars, 8 seconds round, with no drums. A rolling triangle and a soft
    held chord."""
    bars = ['Am', 'Dm', 'Am', 'E']
    s = Sound(BAR * len(bars), M, loop=True)
    for b, chord in enumerate(bars):
        start = b * BAR
        names = CHORDS[chord]
        rolling = [names[0] + '3', names[1] + '4', names[2] + '4', names[0] + '5',
                   names[2] + '4', names[1] + '4', names[0] + '4', names[2] + '3']
        for i, name in enumerate(rolling):
            s.add(start + i * EIGHTH, tone('triangle', EIGHTH * 1.8, M, note(name), 0.5,
                                           decay=0.3))
        # the held chord, faded in and out, two pulses a little out of tune for a soft shimmer
        for name in names[1:]:
            for detune in (1.0, 1.004):
                part = tone('pulse', BAR, M, note(name + '4') * detune, 0.07, duty=0.25)
                part = [v * math.sin(math.pi * i / len(part)) for i, v in enumerate(part)]
                s.add(start, part)
        s.add(start, tone('triangle', BAR, M, note(names[0] + '2'), 0.5, decay=1.0))
    s.write('menumus')


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for make in (crystal, door, light, banish, hurt, step, bump, spotted, won, caught,
                 menu_move, menu_change, start, game_music, menu_music):
        make()
