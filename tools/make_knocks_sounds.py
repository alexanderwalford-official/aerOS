#!/usr/bin/env python3
"""
make_knocks_sounds.py - make every sound of Four Knocks (the knocks package): the wind and rain
round the observatory, the hum of the lower level and the rumble of the borehole, footsteps, the
thing's breathing, footsteps and shriek, the knocks, the telephone, doors, the lift, the
generator, the music, and the voices on the tapes and the telephone line

    python3 tools/make_knocks_sounds.py

needs numpy and scipy, and writes WAV files to packages/knocks/, where pkg install puts them in
/apps/knocks/ for the game to load with sload(). Nothing is recorded or copied: every sound is
worked out here from a few plain ingredients, the way a synthesizer makes them:

    noise        random samples: hiss, wind, breath, rain and rumble are all noise, filtered
    filters      "low pass" keeps the low part of a sound (a rumble), "high pass" the high part
                 (a hiss), "band pass" a band in between (the whistle of wind is a narrow band
                 of noise, moved up and down)
    envelopes    how loud a sound is over time: a knock is loud at once and dies away quickly
    partials     a bell or a piano note is several sine waves at once, at set ratios
    reverb       a sound's echoes in a big space: copies of it, delayed and quieter, fed back
                 into themselves ("comb" and "all-pass" filters, as M. R. Schroeder described)
    formants     a voice is a buzz from the throat shaped by the mouth: each vowel is a few
                 resonant bands ("formants") at its own pitches. Moving them from vowel to vowel
                 over a buzz, with hisses and clicks for the consonants, makes something that
                 sounds like speech through a bad line (the game shows the words as subtitles)

Sounds are mono: effects at 22050 samples a second, the long background sounds and the music at
11025, and the voices at 8000 (they have little high sound in them anyway). The background
sounds and the voices are 8 bit, the rest 16: their hiss is part of them, so the faint hiss 8
bits add doesn't matter, and it halves their size. That keeps the package to a few MB.
Loops (the background sounds, the breathing, the alarm) have their end faded into their start,
so they go round without a click. Running this again makes exactly the same files.
"""

import math
import os
import struct

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'packages', 'knocks')

FX = 22050     # samples a second for effects
LOW = 11025    # for background sounds and music
VOICE = 8000   # and for voices, which come down a telephone line or off old tape anyway

rng = np.random.default_rng(1983)


# ---- ingredients ----------------------------------------------------------------------------------

def times(seconds, rate):
    return np.arange(int(seconds * rate)) / rate


def noise(seconds, rate):
    return rng.standard_normal(int(seconds * rate))


def brown(seconds, rate):
    """Noise with its high part rolled off ("brown" noise): a rumble rather than a hiss."""
    return signal.lfilter([1], [1, -0.995], noise(seconds, rate)) * 0.05


def filt(x, rate, kind, hz, order=2):
    """A Butterworth filter: kind 'low', 'high' or 'band' (hz is then (low, high))."""
    sos = signal.butter(order, hz, btype={'low': 'lowpass', 'high': 'highpass', 'band': 'bandpass'}[kind], fs=rate, output='sos')
    return signal.sosfilt(sos, x)


def resonator(x, rate, hz, q):
    """A single sharp resonance: rings at hz, longer the higher q is (a struck object's mode)."""
    b, a = signal.iirpeak(hz, q, fs=rate)
    return signal.lfilter(b, a, x)


def decay(seconds, rate, time_constant, attack=0.002):
    """An envelope: up in `attack` seconds, then dying away by e every `time_constant` seconds."""
    t = times(seconds, rate)
    env = np.exp(-t / time_constant)
    if attack > 0:
        env *= np.clip(t / attack, 0, 1)
    return env


def place(total, sound, at, rate):
    """Add `sound` into `total` starting `at` seconds in (cut off at the end)."""
    start = int(at * rate)
    end = min(len(total), start + len(sound))
    if start < len(total):
        total[start:end] += sound[:end - start]
    return total


def reverb(x, rate, size=1.0, wet=0.35, damping=0.3):
    """Echoes in a room: four "comb" filters (a sound fed back into itself after a delay, each
    a different delay so they don't build up on one note) added together, then two "all-pass"
    filters that smear the echoes into a wash. size stretches the delays (a bigger room), and
    damping takes the high part out of each echo, as soft walls do."""
    x = np.concatenate([x, np.zeros(int(rate * 1.5 * size))])
    combs = np.zeros_like(x)
    for delay_ms, feedback in ((29.7, 0.84), (37.1, 0.83), (41.1, 0.82), (43.7, 0.81)):
        d = max(1, int(delay_ms * size * rate / 1000))
        # y[n] = x[n] + feedback * (smoothed y[n - d]): the smoothing is the damping
        a = np.zeros(d + 2)
        a[0] = 1
        a[d] = -feedback * (1 - damping)
        a[d + 1] = -feedback * damping
        combs += signal.lfilter([1], a, x)
    out = combs / 4
    for delay_ms in (5.0, 1.7):
        d = max(1, int(delay_ms * size * rate / 1000))
        g = 0.7
        b = np.zeros(d + 1)
        a = np.zeros(d + 1)
        b[0], b[d] = -g, 1
        a[0], a[d] = 1, -g
        out = signal.lfilter(b, a, out)
    return x * (1 - wet) + out * wet


def loop_join(x, rate, overlap=1.0):
    """Make a sound go round smoothly: its last `overlap` seconds faded out over its first ones
    faded in, and the end cut off, so the end leads straight back into the start."""
    n = int(overlap * rate)
    fade = np.linspace(0, 1, n)
    start = x[:n] * fade + x[-n:] * (1 - fade)
    return np.concatenate([start, x[n:-n]])


def save(name, x, rate, peak=0.9, bits=16):
    """Write a mono WAV, as loud as `peak` (1 is the loudest a WAV can hold), 16 or 8 bit. An 8
    bit WAV's samples are 0 to 255 with silence at 128, a 16 bit one's -32768 to 32767."""
    x = np.asarray(x, float)
    top = np.max(np.abs(x))
    if top > 0:
        x = x / top * peak
    if bits == 8:
        data = np.round(np.clip(x, -1, 1) * 127 + 128).astype(np.uint8).tobytes()
    else:
        data = (np.clip(x, -1, 1) * 32767).astype('<i2').tobytes()
    size = bits // 8
    header = b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * size, size, bits)
    header += b'data' + struct.pack('<I', len(data))
    with open(os.path.join(OUT, name + '.wav'), 'wb') as f:
        f.write(header + data)


# ---- the places ----------------------------------------------------------------------------------

def wind():
    """Up top: wind round the building, whistling in a gap, gusting, rain against the windows,
    and now and then the building creaking."""
    seconds = 15
    t = times(seconds, LOW)
    gust = filt(noise(seconds, LOW), LOW, 'low', 0.25)
    gust = 0.55 + gust / np.max(np.abs(gust)) * 0.45
    body = filt(noise(seconds, LOW), LOW, 'band', (150, 700)) * gust
    # the whistle: a narrow band of noise sliding up and down with the gusts
    whistle = np.zeros_like(t)
    hop = 512
    source = noise(seconds, LOW)
    for start in range(0, len(t), hop):
        centre = 520 + 380 * gust[start]
        piece = source[max(0, start - 64):start + hop]
        whistle[start:start + hop] = resonator(piece, LOW, centre, 30)[-len(whistle[start:start + hop]):]
    whistle *= gust ** 3 * 0.35
    rain = np.zeros_like(t)
    for _ in range(seconds * 45):
        drop = filt(noise(0.012, LOW), LOW, 'band', (1500, 4500)) * decay(0.012, LOW, 0.003)
        place(rain, drop * rng.uniform(0.05, 0.3), rng.uniform(0, seconds), LOW)
    creaks = np.zeros_like(t)
    for at in (4.2, 11.0):
        place(creaks, creak_sound(LOW, 1.1, 0.6), at, LOW)
    return loop_join(body * 0.8 + whistle + rain * 0.6 + creaks * 0.25, LOW)


def hum():
    """The lower level: the mains hum of old lights, a low rumble, water dripping somewhere and
    a pipe groaning."""
    seconds = 13
    t = times(seconds, LOW)
    mains = 0.12 * np.sin(2 * np.pi * 50 * t) + 0.06 * np.sin(2 * np.pi * 100 * t) + 0.03 * np.sin(2 * np.pi * 150 * t)
    rumble = filt(brown(seconds, LOW), LOW, 'low', 120) * 6
    drips = np.zeros_like(t)
    for at in (1.3, 5.9, 6.6, 10.2):
        tt = times(0.25, LOW)
        hz = 1300 - 500 * tt / 0.25
        drip = np.sin(2 * np.pi * np.cumsum(hz) / LOW) * decay(0.25, LOW, 0.04)
        place(drips, reverb(drip, LOW, 0.8, 0.5)[:LOW], at, LOW)
    tt = times(3.5, LOW)
    groan_hz = 70 + 25 * np.sin(tt * 1.3) + 8 * np.sin(tt * 7.1)
    groan = signal.sawtooth(2 * np.pi * np.cumsum(groan_hz) / LOW) * np.sin(np.pi * tt / 3.5) ** 2
    groan = filt(groan, LOW, 'band', (60, 400)) * 0.5
    total = mains + rumble + drips * 0.5
    place(total, groan, 7.5, LOW)
    return loop_join(total, LOW)


def deep():
    """The borehole: a deep rumble, air breathing slowly in and out of the shaft, and far-off
    groans of metal."""
    seconds = 13
    t = times(seconds, LOW)
    rumble = filt(brown(seconds, LOW), LOW, 'low', 70) * 10
    breathing = 0.5 + 0.5 * np.sin(2 * np.pi * t / 6.5)
    air = resonator(noise(seconds, LOW), LOW, 180, 6) * breathing * 0.25
    total = rumble + air
    for at, hz in ((3.0, 240), (9.0, 180)):
        tt = times(2.5, LOW)
        tone = np.sin(2 * np.pi * np.cumsum(hz + 30 * np.sin(tt * 3)) / LOW) * np.sin(np.pi * tt / 2.5) ** 3
        place(total, reverb(tone * 0.15, LOW, 2.0, 0.7)[:int(4 * LOW)], at, LOW)
    return loop_join(total, LOW)


# ---- footsteps -------------------------------------------------------------------------------------

def step_soft():
    """A footstep on carpet: a dull thud and a brush of cloth."""
    thud = filt(noise(0.15, FX), FX, 'low', 250) * decay(0.15, FX, 0.025)
    brush = filt(noise(0.15, FX), FX, 'band', (800, 3000)) * decay(0.15, FX, 0.04, 0.02) * 0.15
    return thud + brush


def step_hard():
    """A footstep on concrete: a heel's click, grit, and a thud, with a little echo."""
    click = filt(noise(0.2, FX), FX, 'band', (1200, 5000)) * decay(0.2, FX, 0.008) * 0.6
    grit = filt(noise(0.2, FX), FX, 'high', 3000) * decay(0.2, FX, 0.03, 0.01) * 0.12
    thud = filt(noise(0.2, FX), FX, 'low', 300) * decay(0.2, FX, 0.03)
    return reverb(click + grit + thud, FX, 0.6, 0.2)[:int(0.45 * FX)]


def beast_step():
    """The thing's footstep: heavy, a wet slap, and the dry crackle of joints."""
    thud = np.sin(2 * np.pi * 55 * times(0.4, FX)) * decay(0.4, FX, 0.06) * 0.8
    slap = filt(noise(0.4, FX), FX, 'band', (300, 1500)) * decay(0.4, FX, 0.03)
    slap *= 1 + 0.6 * np.sin(2 * np.pi * 40 * times(0.4, FX))     # a squelch
    total = thud + slap * 0.7
    for k in range(4):
        crack = filt(noise(0.01, FX), FX, 'band', (2000, 6000)) * decay(0.01, FX, 0.0015)
        place(total, crack * 0.5, 0.06 + k * 0.022 + rng.uniform(0, 0.01), FX)
    return reverb(total, FX, 0.7, 0.25)[:int(0.6 * FX)]


# ---- the thing --------------------------------------------------------------------------------------

def breathing():
    """The thing breathing, round and round: a long ragged breath in through something wet, a
    rattling breath out, and a wait."""
    seconds = 4.6
    total = np.zeros(int(seconds * FX))
    tt = times(1.6, FX)
    rough = 0.7 + 0.3 * filt(noise(1.6, FX), FX, 'low', 30) * 8
    inhale = np.zeros_like(tt)
    src = noise(1.6, FX)
    for start in range(0, len(tt), 256):
        piece = src[max(0, start - 128):start + 256]
        centre = 500 + 900 * start / len(tt)
        inhale[start:start + 256] = resonator(piece, FX, centre, 4)[-len(inhale[start:start + 256]):]
    inhale *= np.sin(np.pi * tt / 1.6) ** 1.5 * rough
    place(total, inhale * 0.6, 0.0, FX)
    tt = times(1.9, FX)
    exhale = filt(noise(1.9, FX), FX, 'band', (150, 900))
    exhale *= 1 + 0.8 * np.sin(2 * np.pi * 27 * tt)              # the rattle
    growl = filt(signal.sawtooth(2 * np.pi * 68 * tt), FX, 'low', 300) * 0.25
    exhale = (exhale + growl) * np.sin(np.pi * tt / 1.9) ** 2 * np.exp(-tt / 1.5)
    place(total, exhale, 1.85, FX)
    return loop_join(total, FX, 0.15)


def shriek():
    """The thing's cry when it hears you: several rough tones climbing out of tune with one
    another, a screaming hiss over them, a growl under them, and the echo of the place."""
    seconds = 1.9
    t = times(seconds, FX)
    env = np.clip(t / 0.04, 0, 1) * np.exp(-np.maximum(t - 0.9, 0) / 0.35)
    total = np.zeros_like(t)
    for k, detune in enumerate((1.0, 1.06, 1.13, 0.71)):
        hz = (520 + 700 * np.clip(t / 0.5, 0, 1) - 180 * np.clip((t - 0.9) / 1, 0, 1)) * detune
        hz = hz * (1 + 0.03 * np.sin(2 * np.pi * (7 + k) * t))
        total += signal.sawtooth(2 * np.pi * np.cumsum(hz) / FX) * 0.3
    total *= np.sin(2 * np.pi * 37 * t) * 0.5 + 0.7             # rough, like a ring modulator
    scream = filt(noise(seconds, FX), FX, 'band', (2200, 3800)) * 0.8
    growl = signal.sawtooth(2 * np.pi * 85 * t) * 0.4
    total = np.tanh((total + scream + growl) * env * 2.5)
    return reverb(total, FX, 1.4, 0.35)


def sting():
    """The note when you first see it: a sudden low cluster and a high trembling chord a
    semitone apart, an impact under them, dying away in a long echo."""
    seconds = 3.5
    t = times(seconds, FX)
    env = np.exp(-t / 1.1) * np.clip(t / 0.01, 0, 1)
    low = sum(signal.sawtooth(2 * np.pi * hz * t) for hz in (55, 58.3, 82.4))
    low = filt(low, FX, 'low', 400) * 0.5
    high = sum(np.sin(2 * np.pi * hz * t + 3 * np.sin(2 * np.pi * 6 * t)) for hz in (1568, 1661, 1760))
    high *= 0.5 + 0.5 * np.sin(2 * np.pi * 14 * t)                # tremolo
    hit = filt(noise(seconds, FX), FX, 'low', 600) * decay(seconds, FX, 0.15)
    return reverb((low + high * 0.25) * env + hit, FX, 1.6, 0.4)


def heartbeat():
    """One heartbeat, lub-dub, felt more than heard."""
    total = np.zeros(int(0.6 * FX))
    for at, hz, loud in ((0.0, 48, 1.0), (0.17, 60, 0.7)):
        tt = times(0.12, FX)
        beat = np.sin(2 * np.pi * hz * tt) * np.sin(np.pi * tt / 0.12) * loud
        place(total, beat, at, FX)
    return filt(total, FX, 'low', 150)


def knocks(deep_down=True):
    """Four knocks. From the borehole: deep thuds far below with a long echo. On a door: sharp
    knocks on wood, close by."""
    total = np.zeros(int(4.2 * FX))
    for k in range(4):
        if deep_down:
            tt = times(0.5, FX)
            knock = (np.sin(2 * np.pi * 52 * tt) + 0.5 * np.sin(2 * np.pi * 104 * tt)) * decay(0.5, FX, 0.08)
            knock += filt(noise(0.5, FX), FX, 'low', 400) * decay(0.5, FX, 0.02) * 0.6
        else:
            hit = filt(noise(0.2, FX), FX, 'band', (150, 1200)) * decay(0.2, FX, 0.006)
            knock = resonator(hit, FX, 190, 8) + resonator(hit, FX, 430, 10) * 0.5 + hit * 0.3
        place(total, knock, 0.2 + k * 0.7, FX)
    if deep_down:
        return reverb(total, FX, 2.2, 0.6, 0.5)
    return reverb(total, FX, 0.5, 0.15)


# ---- the building ---------------------------------------------------------------------------------

def creak_sound(rate, seconds=1.3, pitch=1.0):
    """A creak: wood or a hinge sticking and slipping, many small jerks faster and faster, each
    ringing the wood's own resonances."""
    t = times(seconds, rate)
    hz = (40 + 90 * (t / seconds) ** 1.5) * pitch * (1 + 0.15 * filt(noise(seconds, rate), rate, 'low', 20) * 10)
    phase = np.cumsum(hz) / rate
    pulses = (np.diff(np.floor(phase), prepend=0) > 0).astype(float) * rng.uniform(0.5, 1, len(t))
    wood = resonator(pulses, rate, 620 * pitch, 6) + resonator(pulses, rate, 1450 * pitch, 8) * 0.6
    return wood * np.sin(np.pi * t / seconds) ** 0.5


def door_slam():
    """A door slamming somewhere: a heavy thud and the rattle of its frame, echoing down a hall."""
    thud = np.sin(2 * np.pi * 70 * times(0.5, FX)) * decay(0.5, FX, 0.08)
    thud += filt(noise(0.5, FX), FX, 'low', 800) * decay(0.5, FX, 0.03)
    rattle = filt(noise(0.5, FX), FX, 'band', (900, 3000)) * decay(0.5, FX, 0.12) * (np.sin(2 * np.pi * 23 * times(0.5, FX)) > 0.3) * 0.3
    return reverb(thud + rattle, FX, 1.5, 0.45)


def phone_ring():
    """An old telephone's bell: a little hammer beating between two bells twenty times a second,
    for two seconds, then two seconds' quiet (the game plays it round and round)."""
    seconds = 4.0
    t = times(seconds, FX)
    ringing = t < 2.0
    strikes = (np.sin(2 * np.pi * 20 * t) > 0.6) * ringing
    bell = np.zeros_like(t)
    for ratio, loud in ((1.0, 1.0), (2.32, 0.5), (4.25, 0.3), (6.8, 0.15)):
        bell += resonator(strikes.astype(float), FX, 900 * ratio, 60) * loud
    return reverb(bell, FX, 0.7, 0.2)[:len(t)]


def lift_sound():
    """The lift: a clank, the motor straining and the cable groaning as it goes down, a clank."""
    seconds = 7.0
    t = times(seconds, FX)
    running = np.clip(t / 0.6, 0, 1) * np.clip((seconds - 0.7 - t) / 0.6, 0, 1)
    motor = signal.sawtooth(2 * np.pi * (110 + 4 * np.sin(2 * np.pi * 0.7 * t)) * t)
    motor = filt(motor, FX, 'low', 500) * 0.5 + np.sin(2 * np.pi * 870 * t) * 0.04
    cable = creak_sound(FX, seconds, 0.6) * 0.25
    total = (motor + cable) * running
    for at in (0.05, seconds - 0.6):
        place(total, metal_hit(1.0), at, FX)
    return total


def metal_hit(seconds):
    """Something heavy and metal struck once: several inharmonic modes ringing down."""
    hit = filt(noise(seconds, FX), FX, 'band', (100, 3000)) * decay(seconds, FX, 0.01)
    ring = sum(resonator(hit, FX, hz, 40) * loud for hz, loud in ((180, 1), (433, 0.6), (911, 0.4), (1720, 0.2)))
    return reverb(ring * 0.4 + hit * 0.5, FX, 1.0, 0.3)[:int(seconds * FX)]


def click():
    """A flashlight's switch."""
    total = np.zeros(int(0.12 * FX))
    for at in (0.0, 0.035):
        place(total, filt(noise(0.01, FX), FX, 'high', 2500) * decay(0.01, FX, 0.002), at, FX)
    return total


def buzz():
    """A bulb or a flashlight flickering: an electric buzz with crackles."""
    t = times(0.5, FX)
    hum_ = np.sign(np.sin(2 * np.pi * 100 * t)) * 0.3
    crackle = filt(noise(0.5, FX), FX, 'high', 2000) * (rng.random(len(t)) < 0.01) * 3
    return filt(hum_ + crackle, FX, 'band', (90, 5000)) * np.sin(np.pi * t / 0.5)


def paper():
    """Paper picked up and unfolded: crackles of high noise."""
    total = np.zeros(int(0.7 * FX))
    for _ in range(14):
        place(total, filt(noise(0.04, FX), FX, 'band', (1500, 7000)) * decay(0.04, FX, 0.01) * rng.uniform(0.3, 1), rng.uniform(0, 0.6), FX)
    return total


def pickup():
    """Something small and metal picked up: a short clink."""
    hit = filt(noise(0.4, FX), FX, 'high', 1000) * decay(0.4, FX, 0.002)
    return sum(resonator(hit, FX, hz, 80) * loud for hz, loud in ((2400, 1), (3710, 0.6), (5300, 0.3))) + hit * 0.2


def thunder():
    """Thunder close by: a crack, then a long rolling rumble."""
    seconds = 6.0
    crack = filt(noise(seconds, FX), FX, 'high', 800) * decay(seconds, FX, 0.08)
    roll = filt(brown(seconds, FX), FX, 'low', 200) * 25
    roll *= np.clip(times(seconds, FX) / 0.3, 0, 1) * np.exp(-times(seconds, FX) / 2.2)
    roll *= 0.6 + 0.4 * np.abs(filt(noise(seconds, FX), FX, 'low', 3) * 20)
    return reverb(crack * 0.6 + roll, FX, 1.8, 0.3)[:int(seconds * FX)]


def collapse():
    """The charges going off and the shaft coming down: a deep boom, a roar of rock, things
    breaking, groaning metal, slowly settling."""
    seconds = 9.0
    t = times(seconds, LOW)
    boom = np.sin(2 * np.pi * 35 * t) * decay(seconds, LOW, 0.8) * 1.2
    roar = filt(brown(seconds, LOW), LOW, 'low', 300) * 30 * np.exp(-t / 3.5)
    total = boom + roar
    for _ in range(40):
        bit = filt(noise(0.2, LOW), LOW, 'band', (200, 2500)) * decay(0.2, LOW, 0.03) * rng.uniform(0.1, 0.6)
        place(total, bit * np.exp(-rng.uniform(0, 5) / 4), rng.uniform(0.3, 6), LOW)
    tt = times(3.0, LOW)
    groan = np.sin(2 * np.pi * np.cumsum(150 - 40 * tt) / LOW) * np.sin(np.pi * tt / 3) * 0.3
    place(total, groan, 2.0, LOW)
    return reverb(total, LOW, 2.0, 0.4)[:len(t)]


def alarm():
    """The charges' klaxon: two harsh notes, high then low, round and round."""
    t = times(1.0, FX)
    hz = np.where(t < 0.5, 620, 470)
    tone = np.tanh(signal.square(2 * np.pi * np.cumsum(hz) / FX) * 2)
    return filt(tone, FX, 'band', (300, 2500)) * 0.8


def caught():
    """The end: its cry right in your ear, a crunch, and a low boom fading out."""
    total = np.zeros(int(2.6 * FX))
    place(total, shriek()[:int(1.2 * FX)], 0.0, FX)
    crunch = filt(noise(0.3, FX), FX, 'band', (200, 3000)) * decay(0.3, FX, 0.05)
    crunch *= rng.random(len(crunch)) < 0.3
    place(total, crunch * 3, 0.35, FX)
    boom = np.sin(2 * np.pi * 40 * times(2.0, FX)) * decay(2.0, FX, 0.6)
    place(total, boom * 1.5, 0.4, FX)
    return total


def gasp():
    """Your gasp for breath, after holding it too long: a sharp breath in, an "ah"."""
    tt = times(0.7, FX)
    breath = filt(noise(0.7, FX), FX, 'band', (500, 4000)) * np.exp(-tt / 0.25) * np.clip(tt / 0.03, 0, 1)
    vowel = resonator(breath, FX, 800, 5) + resonator(breath, FX, 1200, 6)
    return breath * 0.5 + vowel


def static():
    """Static, like a radio between stations: hiss that comes and goes, crackling. It is what
    your flashlight and your ears pick up when the thing is close."""
    seconds = 2.5
    t = times(seconds, FX)
    hiss = filt(noise(seconds, FX), FX, 'band', (800, 6000))
    flutter = 0.5 + 0.5 * np.abs(filt(noise(seconds, FX), FX, 'low', 12) * 15)
    crackle = (rng.random(len(t)) < 0.004) * rng.standard_normal(len(t)) * 4
    return loop_join(hiss * flutter + crackle, FX, 0.3)


def generator_start():
    """The generator starting: the starter cranking, catching, and running."""
    seconds = 4.0
    t = times(seconds, FX)
    crank = filt(noise(seconds, FX), FX, 'low', 400) * (np.sin(2 * np.pi * 5 * t) > 0.2) * (t < 1.6)
    hz = np.where(t < 1.6, 0, 24)
    running = np.sign(np.sin(2 * np.pi * np.cumsum(hz) / FX)) * (t > 1.6)
    running = filt(running, FX, 'low', 300) * np.clip((t - 1.6) / 0.3, 0, 1)
    return crank * 0.7 + running + metal_hit(seconds) * 0.2


def generator_loop():
    """The generator running, round and round."""
    seconds = 2.0
    t = times(seconds, FX)
    chug = filt(np.sign(np.sin(2 * np.pi * 25 * t)), FX, 'low', 250)
    rattle = filt(noise(seconds, FX), FX, 'band', (400, 1500)) * (np.sin(2 * np.pi * 25 * t) > 0.7) * 0.2
    return chug + rattle


def power_switch():
    """The big switch on the power board: a heavy clunk, and a hum rising as power comes on."""
    seconds = 2.0
    t = times(seconds, FX)
    clunk = metal_hit(0.5)
    rise = np.sin(2 * np.pi * (50 + 50 * np.clip(t / 1.2, 0, 1)) * t) * np.clip(t / 0.4, 0, 1) * np.exp(-t / 1.5) * 0.4
    total = rise.copy()
    place(total, clunk, 0.0, FX)
    return total


def answer_beep():
    """An answering machine's beep before a message."""
    t = times(0.5, VOICE)
    return np.sin(2 * np.pi * 1000 * t) * (t < 0.45) * 0.5


# ---- voices --------------------------------------------------------------------------------------
#
# Each vowel's first three formants (in Hz), roughly as an adult's voice makes them
VOWELS = {
    'a': (730, 1090, 2440), 'e': (530, 1840, 2480), 'i': (300, 2250, 3000),
    'o': (570, 840, 2410), 'u': (330, 1000, 2400), 'y': (400, 1900, 2600),
}
HISSES = {'s': (4000, 7000), 'z': (3500, 6000), 'f': (2500, 6000), 'h': (500, 3000), 'c': (2000, 5000),
          'x': (2500, 6000), 'j': (1800, 4000)}
STOPS = set('ptkbdgq')
NASAL = set('mnlrwv')


def speak(text, seconds, rate, pitch, seed, whisper=False, rise=0.0):
    """Something like speech, `seconds` long, the words of `text` (only used for their sounds:
    vowels, hisses, stops), at a voice's `pitch` in Hz, falling a little through each phrase as
    people's voices do (`rise` > 0 makes it rise instead, a question). Whispered: no buzz, only
    breath. It goes through the formants of each vowel in turn, in frames of 10 ms."""
    local = np.random.default_rng(seed)
    letters = [c for c in text.lower() if c.isalpha() or c in " ,.?"]
    sounds = []
    for c in letters:
        if c in VOWELS:
            sounds.append(('v', VOWELS[c], 1.0))
        elif c in HISSES:
            sounds.append(('h', HISSES[c], 0.6))
        elif c in STOPS:
            sounds.append(('s', None, 0.35))
        elif c in NASAL:
            sounds.append(('n', (280, 1300, 2500), 0.5))
        elif c in ' ,.?':
            sounds.append(('gap', None, 0.4 if c == ' ' else 1.2))
    total_weight = sum(w for _, _, w in sounds) or 1
    n = int(seconds * rate)
    t = np.arange(n) / rate
    # the buzz of the throat: a sawtooth at the voice's pitch, wavering a little (jitter), and
    # falling through the phrase
    contour = pitch * (1 - 0.15 * t / seconds + rise * t / seconds) * (1 + 0.02 * np.sin(2 * np.pi * 5.5 * t))
    contour *= 1 + 0.03 * filt(local.standard_normal(n), rate, 'low', 8) * 5
    buzz_ = signal.sawtooth(2 * np.pi * np.cumsum(contour) / rate) if not whisper else np.zeros(n)
    breath = local.standard_normal(n) * (0.25 if not whisper else 1.0)
    source = filt(buzz_, rate, 'low', 3000) + breath * 0.3
    hiss_src = local.standard_normal(n)
    out = np.zeros(n)
    frame = int(rate * 0.01)
    position = 0.0
    states = [None, None, None]
    for kind, data, weight in sounds:
        length_ = weight / total_weight * seconds
        start, end = int(position * rate), int((position + length_) * rate)
        position += length_
        if end <= start:
            continue
        env = np.sin(np.linspace(0, np.pi, end - start)) ** 0.6
        if kind in ('v', 'n'):
            loud = 1.0 if kind == 'v' else 0.4
            seg = np.zeros(end - start)
            for k, (hz, q) in enumerate(zip(data, (6, 9, 10))):
                hz = min(hz * local.uniform(0.92, 1.08), rate / 2 * 0.9)
                b, a = signal.iirpeak(hz, q, fs=rate)
                if states[k] is None:
                    states[k] = signal.lfilter_zi(b, a) * 0
                y, states[k] = signal.lfilter(b, a, source[start:end], zi=states[k])
                seg += y / (k + 1)
            out[start:end] += seg * env * loud
        elif kind == 'h':
            low, high = data
            high = min(high, rate / 2 * 0.95)
            low = min(low, high * 0.7)
            out[start:end] += filt(hiss_src[start:end], rate, 'band', (low, high)) * env * 0.3
        elif kind == 's':
            burst = min(end - start, int(0.02 * rate))
            out[start:start + burst] += filt(hiss_src[start:start + burst], rate, 'band', (800, min(4000, rate / 2 * 0.9))) * 0.5
    return out


def phone_line(x, rate):
    """A voice over an old telephone line: only its middle band, a little crushed, with hum."""
    x = filt(x, rate, 'band', (300, min(3400, rate / 2 * 0.9)), 4)
    x = np.tanh(x / np.max(np.abs(x)) * 2.2)
    t = np.arange(len(x)) / rate
    return x + 0.02 * np.sin(2 * np.pi * 50 * t) + filt(rng.standard_normal(len(x)), rate, 'high', 1500) * 0.03


def tape(x, rate):
    """A voice on an old reel-to-reel tape: its top dulled, wavering in speed (wow), and hiss."""
    t = np.arange(len(x)) / rate
    wobble = t + 0.002 * np.sin(2 * np.pi * 0.7 * t)       # the tape speeding up and slowing
    x = np.interp(wobble * rate, np.arange(len(x)), x)
    x = filt(x, rate, 'band', (150, 3500), 2)
    x = x / np.max(np.abs(x))
    return x + filt(rng.standard_normal(len(x)), rate, 'high', 2000) * 0.06


def voice(lines, rate, pitch, seed, gap=0.5):
    """Lines of speech one after another, each (text, seconds), a little quiet between them."""
    parts = []
    for i, (text, seconds) in enumerate(lines):
        rise = 0.25 if text.endswith('?') else 0.0
        parts.append(speak(text, seconds, rate, pitch, seed + i, rise=rise))
        parts.append(np.zeros(int(gap * rate)))
    x = np.concatenate(parts)
    return x / np.max(np.abs(x))


# The words, and how long each line takes. The game shows the same words as subtitles, at the
# same times (knocks.aer, the *_LINES lists): change them together
DAD, KIT, ASKEW = 105, 160, 195
MESSAGE = [("Kit. It's me. It's Dad.", 2.2), ("I know how long it's been. I'm sorry.", 2.6),
           ("I'm still at the station. I can't get out.", 2.6), ("Come down to me. Bring my key.", 2.4),
           ("Please, Kit. Come down.", 2.0)]
CALL_DAD_1 = [("Kit. You came.", 1.6)]
CALL_KIT = [("Dad? Dad, is that you?", 1.8)]
CALL_DAD_2 = [("The lift. Bring the key down to me.", 2.4)]
LOG = [("Askew. Ninth of March, nineteen eighty six.", 2.8), ("The knocks are on geophone four now. Not six. Four.", 3.2),
       ("It is coming up the hole.", 1.8), ("Martin wants to stop answering it. I think it is too late for that.", 3.6)]
FINAL = [("Kit. If you're hearing this, it called you.", 2.8), ("That wasn't me on the phone. It never was.", 2.6),
         ("It learns voices. It learned mine.", 2.2), ("I killed the lift so it couldn't follow anyone up.", 2.8),
         ("The survey charges are still in the casing.", 2.4), ("Play it the knocks, and it will come to the sound.", 2.8),
         ("Then set them off, and take the ladder up.", 2.4), ("Don't wait for me. I love you.", 2.4)]
HOME = [("Kit. Come home. I'm already here.", 2.6)]


def message():
    """The answering machine message that starts it all: a beep, then "Dad" on a bad line."""
    v = phone_line(voice(MESSAGE, VOICE, DAD, 10), VOICE)
    return np.concatenate([answer_beep(), np.zeros(int(0.4 * VOICE)), v])


def call():
    """The telephone call in the director's office: "Dad", then you, then "Dad" again, then your
    own words in your own voice, coming back down the line, then four knocks."""
    dad1 = phone_line(voice(CALL_DAD_1, VOICE, DAD, 20), VOICE)
    kit_words = voice(CALL_KIT, VOICE, KIT, 30)
    kit = filt(kit_words, VOICE, 'band', (120, 3800)) * 0.8       # you, in the room
    dad2 = phone_line(voice(CALL_DAD_2, VOICE, DAD, 40), VOICE)
    mimic = phone_line(kit_words, VOICE)                          # the same words, back at you
    # the knocks, down the line: made at FX, every so many samples taken to make it VOICE
    deep = knocks(True)
    deep = np.interp(np.arange(0, len(deep), FX / VOICE), np.arange(len(deep)), deep)[:int(4.5 * VOICE)]
    deep = filt(deep, VOICE, 'band', (300, 3000))
    deep = deep / np.max(np.abs(deep)) * 0.8
    pause = lambda s: np.zeros(int(s * VOICE))
    return np.concatenate([dad1, pause(0.6), kit, pause(0.5), dad2, pause(1.4), mimic, pause(0.8), deep])


def music_title():
    """The title's music: a low drone and an old, out of tune piano picking out a few notes,
    far apart, in a large empty room. 24 seconds, round and round."""
    seconds = 24
    t = times(seconds, LOW)
    drone = filt(signal.sawtooth(2 * np.pi * 55 * t) + signal.sawtooth(2 * np.pi * 55.4 * t) + 0.5 * signal.sawtooth(2 * np.pi * 82.2 * t), LOW, 'low', 220)
    drone *= 0.18 * (0.7 + 0.3 * np.sin(2 * np.pi * t / 12))
    notes = [(0.5, 329.6), (2.0, 261.6), (3.5, 246.9), (6.5, 220.0), (9.0, 329.6), (10.5, 311.1),
             (12.0, 246.9), (15.5, 174.6), (18.0, 220.0), (20.0, 207.7)]
    piano = np.zeros_like(t)
    for at, hz in notes:
        tt = times(4.0, LOW)
        note = np.zeros_like(tt)
        # partials a little sharp of whole multiples, as a piano's strings are, and a second
        # string a little out of tune with the first: it beats, sourly
        for k in range(1, 6):
            partial_hz = hz * k * (1 + 0.0008 * k * k)
            if partial_hz > LOW / 2 * 0.9:
                break
            note += (np.sin(2 * np.pi * partial_hz * tt) + np.sin(2 * np.pi * partial_hz * 1.004 * tt)) / k
        note *= decay(4.0, LOW, 0.9 / (1 + hz / 600), 0.004)
        place(piano, note * 0.25, at, LOW)
    total = reverb(drone + piano, LOW, 2.2, 0.55)
    return loop_join(total[:len(t) + int(LOW)], LOW, 1.0)


def music_end():
    """The ending's music: the same piano, slower, settling on a chord that isn't quite at rest."""
    seconds = 22
    t = times(seconds, LOW)
    notes = [(0.5, 220.0), (2.5, 261.6), (4.5, 329.6), (7.5, 293.7), (10.0, 220.0), (12.0, 246.9),
             (14.0, 196.0), (16.5, 220.0), (16.5, 277.2), (16.5, 329.6)]
    piano = np.zeros(len(t) + LOW * 4)
    for at, hz in notes:
        tt = times(5.0, LOW)
        note = sum((np.sin(2 * np.pi * hz * k * (1 + 0.0008 * k * k) * tt) + np.sin(2 * np.pi * hz * k * 1.003 * tt)) / k
                   for k in range(1, 5))
        note *= decay(5.0, LOW, 1.2, 0.004)
        place(piano, note * 0.25, at, LOW)
    total = reverb(piano, LOW, 2.4, 0.55)
    return total[:len(t)] * np.clip((seconds - t) / 3, 0, 1)


def main():
    os.makedirs(OUT, exist_ok=True)
    sounds = [
        ('wind', wind(), LOW, 0.8, 8), ('hum', hum(), LOW, 0.8, 8), ('deep', deep(), LOW, 0.8, 8),
        ('step1', step_soft(), FX, 0.7), ('step2', step_hard(), FX, 0.7),
        ('bstep', beast_step(), FX, 0.9), ('breath', breathing(), FX, 0.8), ('shriek', shriek(), FX, 0.95),
        ('sting', sting(), FX, 0.9), ('heart', heartbeat(), FX, 0.9),
        ('knocks', knocks(True), FX, 0.9), ('knockd', knocks(False), FX, 0.9),
        ('creak', creak_sound(FX), FX, 0.7), ('slam', door_slam(), FX, 0.9), ('ring', phone_ring(), FX, 0.7),
        ('lift', lift_sound(), FX, 0.8), ('click', click(), FX, 0.6), ('buzz', buzz(), FX, 0.6),
        ('paper', paper(), FX, 0.6), ('pickup', pickup(), FX, 0.6), ('thunder', thunder(), FX, 0.95),
        ('collapse', collapse(), LOW, 0.95), ('alarm', alarm(), FX, 0.7), ('caught', caught(), FX, 0.95),
        ('gasp', gasp(), FX, 0.8), ('static', static(), FX, 0.6), ('genstart', generator_start(), FX, 0.8),
        ('genloop', loop_join(generator_loop(), FX, 0.2), FX, 0.7), ('switch', power_switch(), FX, 0.8),
        ('whisper', reverb(speak('kiit', 1.0, FX, 0, 77, whisper=True), FX, 1.2, 0.5), FX, 0.7),
        ('message', message(), VOICE, 0.85, 8), ('call', call(), VOICE, 0.85, 8),
        ('log', tape(voice(LOG, VOICE, ASKEW, 50), VOICE), VOICE, 0.85, 8),
        ('final', tape(voice(FINAL, VOICE, DAD, 60), VOICE), VOICE, 0.85, 8),
        ('home', phone_line(np.concatenate([answer_beep(), np.zeros(int(0.4 * VOICE)), voice(HOME, VOICE, KIT, 30)]), VOICE), VOICE, 0.85, 8),
        ('title', music_title(), LOW, 0.8), ('end', music_end(), LOW, 0.8),
    ]
    total = 0
    for name, x, rate, peak, *bits in sounds:
        save(name, x, rate, peak, *bits)
        total += len(x) * (bits[0] // 8 if bits else 2)
    print(f'{len(sounds)} sounds written to {OUT}, {total // 1024} KB')


if __name__ == '__main__':
    main()
