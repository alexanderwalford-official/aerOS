#!/usr/bin/env python3
"""
make_lantern_sounds.py - make Lantern's music and sound effects, in the style of old game chips

    python3 tools/make_lantern_sounds.py

writes them to packages/lantern/, where pkg install puts them in /apps/lantern/ next to the
game (packages/lantern.aer, which loads them with sload and plays them with splay). Nothing is
recorded or sampled: every sound is made here from plain waves, the way a 1980s sound chip
made them (see tools/make_maze3d_sounds.py, which this follows):

    pulse      a square wave, on for part of each cycle ("duty"): the lead voice
    triangle   softer, for bass lines
    sine       for bells, plucks and the kick drum
    noise      random values, for drums, swishes and fire

The tunes are written as text, a note and its length in eighths at a time ("E5:2 D5:1 -:1",
where "-" is a rest), over a chord a bar. The files are 8 bit mono WAVs to keep the package
small: effects at 16000 samples a second, music at 11025. The music loops: anything still
ringing at the end wraps round to the start, so it loops without a click. Running this again
makes exactly the same files (the noise comes from a fixed seed).
"""

import math
import os
import random
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'lantern')

EFFECT_RATE = 16000
MUSIC_RATE = 11025

noise = random.Random(7).uniform   # the same "random" noise every time


def note(name):
    """A note's frequency in Hz, from its name: 'A4' is 440, 'C#5', 'Bb3'..."""
    steps = {'C': -9, 'D': -7, 'E': -5, 'F': -4, 'G': -2, 'A': 0, 'B': 2}[name[0]]
    rest = name[1:]
    if rest.startswith('#'):
        steps, rest = steps + 1, rest[1:]
    elif rest.startswith('b'):
        steps, rest = steps - 1, rest[1:]
    return 440.0 * 2 ** ((steps + 12 * (int(rest) - 4)) / 12)


# ---- waves --------------------------------------------------------------------------------------

TABLE = 1024
tables = {}


def table(shape, hz, rate, duty=0.5):
    """One cycle of a pulse or triangle wave, with only the harmonics that fit below half the
    sample rate (so high notes don't whistle)."""
    harmonics = max(1, int(rate / 2 / hz))
    key = (shape, harmonics, duty)
    if key not in tables:
        cycle = [0.0] * TABLE
        for k in range(1, min(harmonics, 40) + 1):
            if shape == 'pulse':
                amount = 2 / (k * math.pi) * math.sin(k * math.pi * duty)
                phase = 0.0
            elif k % 2 == 1:
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
    """A note: 'pulse', 'triangle' or 'sine'. It can glide from hz to slide_to, fade away
    (decay: seconds to fall to about a third) and wobble (vibrato, a fraction of the pitch)."""
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
        value = math.sin(2 * math.pi * phase) if cycle is None else cycle[int(phase * TABLE)]
        level = min(1.0, t / attack) if attack else 1.0
        if decay:
            level *= math.exp(-t / decay)
        level *= min(1.0, (seconds - t) / 0.01)
        out[i] = value * level * volume
    return out


def hiss(seconds, rate, volume=1.0, decay=0.05, smooth=0.0):
    """Noise that fades away; `smooth` (0 to nearly 1) takes the top off it, for a thump."""
    out = []
    last = 0.0
    for i in range(int(seconds * rate)):
        last = last * smooth + noise(-1, 1) * (1 - smooth)
        out.append(last * volume * math.exp(-i / rate / decay))
    return out


class Sound:
    """A sound being put together: parts are added in at their times, and add up."""

    def __init__(self, seconds, rate, loop=False):
        self.rate = rate
        self.loop = loop
        self.samples = [0.0] * int(seconds * rate)

    def add(self, at, part):
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
        """An 8 bit mono WAV, as loud as it can be without clipping. 8.3 names only."""
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


# ---- sound effects --------------------------------------------------------------------------------

R = EFFECT_RATE


def effect(seconds):
    return Sound(seconds, R)


def make_effects():
    s = effect(0.18)                      # a sword swung: a swish of noise that rises
    s.add(0, hiss(0.16, R, 0.5, decay=0.06, smooth=0.3))
    s.add(0, tone('sine', 0.12, R, 300, 0.3, decay=0.05, slide_to=900))
    s.write('swing')

    s = effect(0.2)                       # a hit: a thump and a short crunch
    s.add(0, tone('pulse', 0.12, R, 220, 0.6, duty=0.25, decay=0.05, slide_to=80))
    s.add(0, hiss(0.1, R, 0.5, decay=0.03))
    s.write('hit')

    s = effect(0.35)                      # you are hurt: a falling buzz
    s.add(0, tone('pulse', 0.3, R, 400, 0.7, duty=0.5, decay=0.15, slide_to=110))
    s.add(0, hiss(0.15, R, 0.3, decay=0.05, smooth=0.5))
    s.write('hurt')

    s = effect(0.45)                      # a monster beaten: a pop and a puff
    s.add(0, tone('pulse', 0.08, R, 900, 0.4, duty=0.125, decay=0.04, slide_to=1800))
    s.add(0.05, hiss(0.35, R, 0.45, decay=0.12, smooth=0.6))
    s.write('kill')

    s = effect(0.3)                       # a coin: two bright notes
    s.add(0, tone('pulse', 0.06, R, note('B5'), 0.4, duty=0.25))
    s.add(0.06, tone('pulse', 0.22, R, note('E6'), 0.4, duty=0.25, decay=0.1))
    s.write('coin')

    s = effect(1.3)                       # something that matters found: a rising fanfare
    for i, name in enumerate(['C5', 'E5', 'G5', 'C6']):
        s.add(i * 0.1, tone('pulse', 0.12, R, note(name), 0.35, duty=0.25))
    s.add(0.4, tone('pulse', 0.8, R, note('E6'), 0.35, duty=0.25, decay=0.4, vibrato=0.01))
    s.add(0.4, tone('triangle', 0.8, R, note('C5'), 0.5, decay=0.4))
    s.add(0.4, tone('triangle', 0.8, R, note('G5'), 0.4, decay=0.4))
    s.write('item')

    s = effect(0.04)                      # a letter of speech appearing
    s.add(0, tone('pulse', 0.035, R, 620, 0.3, duty=0.25))
    s.write('talk')

    s = effect(0.6)                       # stairs: footsteps going down, lower and lower
    for i in range(4):
        s.add(i * 0.12, tone('triangle', 0.1, R, 300 - i * 50, 0.6, decay=0.04))
        s.add(i * 0.12, hiss(0.05, R, 0.2, decay=0.02, smooth=0.5))
    s.write('stairs')

    s = effect(0.8)                       # healed: a shimmering run upwards
    for i, name in enumerate(['C5', 'E5', 'G5', 'B5', 'D6', 'G6']):
        s.add(i * 0.07, tone('sine', 0.4, R, note(name), 0.4, decay=0.15))
    s.write('heal')

    s = effect(0.5)                       # a chest's lid: a creak and a clunk
    s.add(0, tone('pulse', 0.25, R, 180, 0.3, duty=0.125, vibrato=0.08, slide_to=260))
    s.add(0.25, tone('sine', 0.2, R, 120, 0.8, decay=0.06, slide_to=60))
    s.write('chest')

    s = effect(0.07)                      # moving round a menu
    s.add(0, tone('pulse', 0.06, R, note('A5'), 0.3, duty=0.125, decay=0.03))
    s.write('select')

    s = effect(0.25)                      # choosing on a menu
    s.add(0, tone('pulse', 0.07, R, note('E5'), 0.35, duty=0.25))
    s.add(0.07, tone('pulse', 0.15, R, note('A5'), 0.35, duty=0.25, decay=0.08))
    s.write('confirm')

    s = effect(2.0)                       # the raid's war horn: low, swelling, wavering
    s.add(0, tone('pulse', 1.9, R, note('D3'), 0.5, duty=0.3, attack=0.25, vibrato=0.01))
    s.add(0, tone('pulse', 1.9, R, note('A3'), 0.3, duty=0.3, attack=0.3, vibrato=0.012))
    s.add(0.6, tone('triangle', 1.3, R, note('D2'), 0.6, attack=0.2))
    s.write('horn')

    s = effect(2.2)                       # the evening bell: three slow tolls
    for i in range(3):
        s.add(i * 0.6, tone('sine', 1.2, R, note('G4'), 0.6, decay=0.5))
        s.add(i * 0.6, tone('sine', 1.2, R, note('G4') * 2.4, 0.2, decay=0.25))
    s.write('bell')

    s = effect(0.6)                       # caught stealing: two sharp falling notes
    s.add(0, tone('pulse', 0.2, R, note('E5'), 0.5, duty=0.5, slide_to=note('C5')))
    s.add(0.22, tone('pulse', 0.3, R, note('D5'), 0.5, duty=0.5, decay=0.15, slide_to=note('A4')))
    s.write('thief')

    s = effect(1.4)                       # the lair's seal breaking: a rumble and a rising tone
    s.add(0, hiss(1.3, R, 0.6, decay=0.5, smooth=0.85))
    s.add(0.1, tone('triangle', 1.2, R, 80, 0.6, slide_to=40))
    s.add(0.3, tone('sine', 1.0, R, 400, 0.3, decay=0.4, slide_to=1600))
    s.write('gate')

    s = effect(1.2)                       # the villain's roar: a low growl that rises and falls
    s.add(0, tone('pulse', 1.1, R, 90, 0.6, duty=0.3, attack=0.1, vibrato=0.08, slide_to=60))
    s.add(0, hiss(1.0, R, 0.4, decay=0.6, smooth=0.75))
    s.write('roar')

    s = effect(1.6)                       # you fall: a long slide down
    s.add(0, tone('triangle', 1.5, R, note('A4'), 0.7, decay=0.8, vibrato=0.02, slide_to=note('A2')))
    s.add(0, tone('pulse', 0.8, R, note('E4'), 0.2, duty=0.125, decay=0.4, slide_to=note('E2')))
    s.write('death')

    s = effect(0.2)                       # a bush cut down
    s.add(0, hiss(0.18, R, 0.6, decay=0.05, smooth=0.1))
    s.write('cut')

    s = effect(0.5)                       # a fireball thrown
    s.add(0, hiss(0.45, R, 0.6, decay=0.2, smooth=0.6))
    s.add(0, tone('sine', 0.3, R, 200, 0.4, decay=0.1, slide_to=90))
    s.write('fire')

    s = effect(1.0)                       # saved, or prayed: a soft chord
    for name in ['G4', 'B4', 'D5', 'G5']:
        s.add(0, tone('sine', 1.0, R, note(name), 0.3, attack=0.05, decay=0.4))
    s.write('save')

    s = effect(0.9)                       # a new aim in the quest: a question that resolves
    s.add(0, tone('pulse', 0.15, R, note('D5'), 0.35, duty=0.25))
    s.add(0.15, tone('pulse', 0.15, R, note('G5'), 0.35, duty=0.25))
    s.add(0.3, tone('pulse', 0.55, R, note('F#5'), 0.35, duty=0.25, decay=0.3, vibrato=0.01))
    s.write('quest')


# ---- music ----------------------------------------------------------------------------------------

M = MUSIC_RATE

CHORDS = {
    'C': ['C', 'E', 'G'], 'Dm': ['D', 'F', 'A'], 'Em': ['E', 'G', 'B'], 'F': ['F', 'A', 'C'],
    'G': ['G', 'B', 'D'], 'Am': ['A', 'C', 'E'], 'Bb': ['Bb', 'D', 'F'], 'D': ['D', 'F#', 'A'],
    'Cm': ['C', 'Eb', 'G'], 'Ab': ['Ab', 'C', 'Eb'], 'Eb': ['Eb', 'G', 'Bb'], 'Fm': ['F', 'Ab', 'C'],
    'E': ['E', 'G#', 'B'], 'A': ['A', 'C#', 'E'], 'Gm': ['G', 'Bb', 'D'], 'Bm': ['B', 'D', 'F#'],
}


def kick(volume=1.0):
    return tone('sine', 0.15, M, 130, volume, decay=0.05, slide_to=40)


def snare(volume=0.35):
    return hiss(0.12, M, volume, decay=0.04, smooth=0.2)


def hat(volume=0.12):
    return hiss(0.03, M, volume, decay=0.01)


def melody(s, start, eighth, text, shape='pulse', volume=0.3, duty=0.25, decay=None, vibrato=0.006):
    """Play a tune written as "E5:2 D5:1 -:1" (a note and its length in eighths, "-" a rest)."""
    at = start
    for word in text.split():
        name, length = word.split(':')
        length = float(length)
        if name != '-':
            s.add(at, tone(shape, length * eighth * 0.95, M, note(name), volume, duty=duty,
                           decay=decay, vibrato=vibrato))
        at += length * eighth


def song(name, bpm, bars, tune, bass='walk', arp=True, drums='rock', lead='pulse', lead_volume=0.3,
         bass_octave=2, arp_volume=0.14, lead_decay=None, tune_bar=0):
    """A looping song: a chord a bar (`bars`), a bass line, a quiet arpeggio of each chord,
    drums, and a tune starting at bar `tune_bar`."""
    beat = 60.0 / bpm
    eighth = beat / 2
    bar = beat * 4
    s = Sound(bar * len(bars), M, loop=True)
    for b, chord in enumerate(bars):
        start = b * bar
        notes = CHORDS[chord]
        root = notes[0]
        if bass == 'walk':      # the root, pushing forwards
            for e, octave in [(0, 0), (2, 0), (3, 1), (4, 0), (6, 0), (7, 1)]:
                s.add(start + e * eighth, tone('triangle', eighth * 0.9, M,
                                               note(root + str(bass_octave + octave)), 0.9, decay=0.2))
        elif bass == 'drive':   # eighths on the root, without a break
            for e in range(8):
                s.add(start + e * eighth, tone('triangle', eighth * 0.85, M,
                                               note(root + str(bass_octave + (e % 4 == 3))), 0.9))
        elif bass == 'slow':    # a long note on the root, then the fifth
            s.add(start, tone('triangle', bar * 0.5, M, note(root + str(bass_octave)), 0.9, decay=1.0))
            s.add(start + bar / 2, tone('triangle', bar * 0.5, M, note(notes[2] + str(bass_octave)), 0.8, decay=1.0))
        if arp:
            tones = [n + '4' for n in notes] + [root + '5']
            for i, k in enumerate([0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2, 1]):
                s.add(start + i * eighth / 2, tone('pulse', eighth / 2, M, note(tones[k]), arp_volume,
                                                   duty=0.125, decay=0.06))
        if drums == 'rock':
            for b4 in range(4):
                s.add(start + b4 * beat, kick() if b4 % 2 == 0 else snare())
                s.add(start + b4 * beat + eighth, hat())
        elif drums == 'march':
            for e in range(8):
                if e in (0, 3, 6):
                    s.add(start + e * eighth, kick(0.8))
                if e in (2, 6):
                    s.add(start + e * eighth, snare(0.3))
                s.add(start + e * eighth, hat(0.08))
        elif drums == 'heart':   # a slow heartbeat, for the dark
            s.add(start, kick(0.7))
            s.add(start + eighth * 0.7, kick(0.5))
    if tune:
        melody(s, tune_bar * bar, eighth, tune, shape=lead, volume=lead_volume, decay=lead_decay)
    s.write(name)


def make_music():
    # The title: wistful, in D minor, no drums. A tune that rises like a lantern being lit
    song('title', 96, ['Dm', 'Bb', 'F', 'C', 'Dm', 'Bb', 'Gm', 'A'],
         "D5:2 E5:1 F5:1 A5:3 G5:1 F5:2 E5:2 D5:2 C5:2 "
         "D5:2 F5:2 E5:4 -:4 A4:4 "
         "D5:2 E5:1 F5:1 A5:3 C6:1 Bb5:2 A5:2 G5:2 F5:2 "
         "E5:2 F5:1 G5:1 E5:4 C#5:8",
         bass='slow', drums=None, lead='triangle', lead_volume=0.55, arp_volume=0.11)

    # Out in the vale by day: bright, in G major, walking pace
    song('day', 120, ['G', 'C', 'G', 'D', 'Em', 'C', 'Am', 'D', 'G', 'C', 'G', 'D', 'Em', 'C', 'D', 'G'],
         "G5:2 B5:2 D6:3 B5:1 C6:2 B5:2 A5:4 "
         "G5:2 B5:2 D6:2 G6:2 F#6:4 -:4 "
         "E6:2 D6:2 B5:3 G5:1 C6:2 B5:2 A5:4 "
         "C6:2 B5:2 A5:2 F#5:2 G5:4 D5:4 "
         "G5:2 B5:2 D6:3 B5:1 C6:2 B5:2 A5:4 "
         "G5:2 B5:2 D6:2 G6:2 F#6:4 -:4 "
         "E6:2 D6:2 B5:2 G5:2 C6:2 E6:2 D6:2 C6:2 "
         "B5:2 A5:2 F#5:2 A5:2 G5:6 -:2")

    # Night: slow and quiet, in A minor, a heartbeat and plucked bells
    song('night', 72, ['Am', 'F', 'Dm', 'E'],
         "E5:3 C5:1 A4:4 F5:3 E5:1 C5:4 D5:3 F5:1 A5:4 G#5:6 -:2",
         bass='slow', drums='heart', lead='sine', lead_volume=0.5, lead_decay=0.6, arp_volume=0.07)

    # Underground: tense, in E with a flat second, the bass never resting
    song('dungeon', 100, ['Em', 'F', 'Em', 'Dm'],
         "-:4 B4:2 C5:2 B4:4 -:4 -:4 E5:2 F5:2 E5:4 -:4",
         bass='drive', drums='march', lead='pulse', lead_volume=0.22, arp_volume=0.09)

    # The villain, and the raids: fast and driving, in C minor
    song('boss', 150, ['Cm', 'Ab', 'Bb', 'G', 'Cm', 'Ab', 'Fm', 'G'],
         "C6:2 Bb5:1 G5:1 Eb5:2 G5:2 Ab5:3 G5:1 F5:2 Eb5:2 "
         "F5:2 G5:2 Bb5:2 D6:2 B5:6 G5:2 "
         "C6:2 Eb6:1 D6:1 C6:2 G5:2 Ab5:2 C6:2 Bb5:2 Ab5:2 "
         "G5:2 F5:2 Eb5:2 D5:2 B4:4 D5:4",
         bass='drive', drums='rock', lead_volume=0.3)

    # The ending: warm, in F major, gentle drums
    song('ending', 88, ['F', 'C', 'Dm', 'Bb', 'F', 'C', 'Bb', 'C'],
         "A5:3 G5:1 F5:2 C5:2 D5:2 E5:2 F5:4 "
         "A5:2 Bb5:2 C6:4 Bb5:2 A5:2 G5:4 "
         "A5:3 G5:1 F5:2 A5:2 C6:2 D6:2 C6:4 "
         "Bb5:2 A5:2 G5:2 E5:2 F5:8",
         bass='walk', drums='march', lead='triangle', lead_volume=0.55)


def main():
    os.makedirs(OUT, exist_ok=True)
    make_effects()
    make_music()


if __name__ == '__main__':
    main()
