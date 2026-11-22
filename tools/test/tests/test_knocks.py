"""Four Knocks (packages/knocks.aer), the horror game: booted from the ISO so GRUB sets up a
graphics screen, with the game installed the way pkg install would (t.package).

The game reads its arguments for testing: "knocks demo" plays itself from the title to the
first ending (the thing can't catch it), "knocks start 2" starts on level B2, and "knocks safe"
makes the thing harmless. In graphics mode the text screen can't be read, so these look at the
serial log (where the game's print()s and any error go) and at screenshots' pixels."""

import os
import struct
import time
import wave

from aeros import read_disk_file



def colours(shot):
    """How many different colours a screenshot has (a sample of its pixels, for speed)."""
    width, height, pixels = shot
    step = 3 * 97   # every 97th pixel
    return len({pixels[i:i + 3] for i in range(0, len(pixels) - 2, step)})


def colours_in(shot, left, top, right, bottom):
    """How many different colours there are in a box of a screenshot (every pixel of it)."""
    width, height, pixels = shot
    return len({pixels[(y * width + x) * 3:(y * width + x) * 3 + 3]
                for y in range(top, bottom) for x in range(left, right)})


def has_colour(shot, colour):
    """Whether a screenshot has a pixel of this colour (0xRRGGBB) anywhere."""
    width, height, pixels = shot
    want = colour.to_bytes(3, 'big')
    found = pixels.find(want)
    while found != -1:
        if found % 3 == 0:
            return True
        found = pixels.find(want, found + 1)
    return False


# Where to keep pictures of the game, for a person to look at (set KNOCKS_SHOTS to a folder)
SHOTS = os.environ.get('KNOCKS_SHOTS')


def keep(aeros, name):
    """Take a screenshot, keep a PNG of it in SHOTS (if set), and give it back."""
    shot = aeros.screenshot(name)
    if SHOTS:
        from PIL import Image
        width, height, pixels = shot
        Image.frombytes('RGB', (width, height), pixels).save(os.path.join(SHOTS, f'knocks-{name}.png'))
    return shot


def boot_with_knocks(t, sound=None):
    aeros = t.boot(graphics=True, sound=sound, disk=t.disk(size_mb=48, files=t.package('knocks')))
    aeros.first_login()
    return aeros


def test_knocks_demo(t):
    # The whole game, played by itself: title, the message, the ground floor, B2, the casing,
    # the charges, the ladder and the first ending
    aeros = boot_with_knocks(t)
    # (KNOCKS_DEMO=2 starts it on B2, to try a change quickly)
    aeros.run('knocks demo ' + os.environ.get('KNOCKS_DEMO', ''), 3)
    start = time.time()
    shot = 0
    next_shot = 0
    # moments to catch as they happen (the demo prints them): the first note, the first flash
    # of lightning, the telephone, and the first time the thing is in your beam
    moments = {'demo: lightning': 'lightning', 'demo: it is in the beam': 'sighting',
               'demo: read at 19, 13': 'note', 'demo: answer the telephone': 'telephone'}
    while 'THE END' not in aeros.serial():
        printed = aeros.serial()
        if time.time() - start > 900:
            done = [line for line in printed.split('\n') if line.startswith('demo:')]
            aeros.fail('the demo did not reach the end in 15 minutes. It did:\n' + '\n'.join(done))
        if 'Error' in printed:
            aeros.fail('an error in the game')
        for words in [w for w in moments if w in printed]:
            keep(aeros, moments.pop(words))
        if time.time() >= next_shot:
            keep(aeros, f'demo{shot:02d}')
            shot += 1
            next_shot = time.time() + 6
        time.sleep(0.3)
    aeros.wait_for('Thanks for playing Four Knocks.', 60)
    aeros.expect_not('Error')


# knocks.aer's colours: the title's words, and the "IT HEARD YOU" when it catches you
TITLE = 0xD8D0C0
RED = 0xB0201C
PAUSED = 0xD9D4C8


def test_knocks_title(t):
    # The content warning, then the title: the far end of a corridor on B2 by flashlight (with
    # the thing in it), the name and the menu. Escape quits
    aeros = boot_with_knocks(t)
    aeros.run('knocks', 8)
    warning = keep(aeros, 'warning')
    if not has_colour(warning, RED):
        aeros.fail('no content warning first')
    aeros.key('spc')
    time.sleep(4)
    title = keep(aeros, 'title')
    width, height, _ = title
    if not has_colour(title, TITLE):
        aeros.fail('the title screen has no title')
    # the corridor, lit by the flashlight in the middle of the picture
    if colours_in(title, width // 2 - 60, height // 2 - 40, width // 2 + 60, height // 2 + 40) < 20:
        aeros.fail('the title screen shows no corridor')
    aeros.key('esc')
    aeros.wait_for('Thanks for playing Four Knocks.', 30)
    aeros.expect_not('Error')


def test_knocks_caught(t):
    # A scripted scare, and dying: start on B2 (it saves your progress there first, so AerOS
    # asks if the game may write in its folder), and walk east down the corridor. The thing
    # crosses it ahead, and walking on into it, it has you: its face, then IT HEARD YOU. A key
    # starts B2 again. The save file says level 2, with a full battery.
    disk = t.disk(size_mb=48, files=t.package('knocks'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    aeros.ask('knocks start 2')
    time.sleep(8)
    aeros.key('spc')                  # past the content warning
    # AerOS asks if it may save in /apps/knocks/: in a box over the graphics, which (unlike a
    # question on the text screen) leaves nothing in the serial log, so wait a moment for it
    time.sleep(5)
    keep(aeros, 'may_it_save')
    aeros.key('a')
    time.sleep(3)
    keep(aeros, 'b2')
    aeros.key('esc')
    time.sleep(1.5)
    keep(aeros, 'paused_first')
    aeros.key('esc')
    time.sleep(1)
    start = time.time()
    # (watched in screenshots: the serial log only catches up with print() some seconds later).
    # The face it shows as it catches you is kept, if a screenshot lands in that second
    while not has_colour(shot := keep(aeros, 'walking'), RED):
        if colours(shot) > 1100:
            keep(aeros, 'grainy')     # the face, or the static of the thing close by
        if time.time() - start > 90:
            aeros.fail('walking down the corridor, the thing never caught you')
        aeros.key('up', 500)
        time.sleep(0.55)     # (key() returns at once: let go before pressing it again)
    keep(aeros, 'caught')
    # (keys pressed in its first second are thrown away, so one held while being caught
    # doesn't skip it)
    time.sleep(2)
    aeros.key('spc')
    time.sleep(4)
    if has_colour(keep(aeros, 'again'), RED):
        aeros.fail('a key after being caught did not start B2 again')
    # Escape pauses, Q goes back to the title (which offers to carry on from B2), Escape quits
    aeros.key('esc')
    time.sleep(1.5)
    # (the pause screen darkens everything but PAUSED, in the game's text colour)
    if not has_colour(keep(aeros, 'paused'), PAUSED):
        aeros.fail('Escape did not pause the game after starting B2 again')
    aeros.key('q')
    time.sleep(8)
    keep(aeros, 'title_continue')
    aeros.key('esc')
    aeros.wait_for('Thanks for playing Four Knocks.', 30)
    aeros.expect_not('Error')
    aeros.quit()
    saved = read_disk_file(disk, '/apps/knocks/save.txt')
    if saved is None or not saved.startswith(b'2 '):
        aeros.fail(f'the progress saved is {saved!r}, not level 2')


# A program trying the built-ins Four Knocks added to AerLang, each in its own 100 x 100 square
# along the top of the screen, for the test to look at
EFFECTS = '''gopen()
gclear(0)
rect(0, 0, 100, 100, 0xFFFFFF)
gtint(0, 0, 100, 100, 0, 0.5)
gnoise(100, 0, 100, 100, 60)
rect(200, 0, 100, 100, 0xFF0000)
imgcol(rdb("/bin/props.bmp"), 200, 0, 100, 100, 64, 128, 1.0, true)
let wall = rdb("/bin/concrete.bmp")
imglight(900, 700, 10, 0.5)
imgcol(wall, 300, 0, 100, 100, 0, 64, 1.0)
imglight(900, 700, 10)
imgcol(wall, 400, 0, 100, 100, 0, 64, 1.0)
imglight()
text(0, 200, "READY", 0xFFFFFF, 1)
key()
'''


def test_knocks_effects(t):
    # gtint, gnoise, imgcol's see-through sprites and imglight's ambient light
    with open(t.path('fx.aer'), 'w') as f:
        f.write(EFFECTS)
    files = {'/bin/fx.aer': t.path('fx.aer'), '/bin/props.bmp': t.repo('packages', 'knocks', 'props.bmp'),
             '/bin/concrete.bmp': t.repo('packages', 'knocks', 'concrete.bmp')}
    aeros = t.boot(graphics=True, disk=t.disk(files=files))
    aeros.first_login()
    aeros.ask('fx', 5)
    shot = keep(aeros, 'effects')
    width, height, pixels = shot
    # white tinted half way to black is mid grey
    if pixels[(50 * width + 50) * 3:(50 * width + 50) * 3 + 3] != b'\x7f\x7f\x7f':
        aeros.fail(f'gtint: white half way to black is {pixels[(50 * width + 50) * 3:(50 * width + 50) * 3 + 3].hex()}')
    if colours_in(shot, 100, 0, 200, 100) < 20:
        aeros.fail('gnoise made no grain')
    # the chair (props.bmp's second picture) over red: red shows round it, and the chair is there
    sprite = colours_in(shot, 200, 0, 300, 100)
    if not has_colour(shot, 0xFF0000) or sprite < 6:
        aeros.fail(f'imgcol see-through: {sprite} colours, red showing: {has_colour(shot, 0xFF0000)}')
    if pixels[(5 * width + 205) * 3:(5 * width + 205) * 3 + 3] != b'\xff\x00\x00':
        aeros.fail('imgcol see-through drew over the see-through top corner of the chair picture')
    if colours_in(shot, 300, 0, 400, 100) < 6:
        aeros.fail("imglight's ambient light did not light the wall outside the beam")
    if colours_in(shot, 400, 0, 500, 100) != 1:
        aeros.fail('with no ambient light, outside the beam is not black')
    aeros.key('spc')
    time.sleep(2)
    aeros.expect_not('Error')


PAN = '''let alarm = sload("/bin/alarm.wav")
let voice = splay(alarm, true, 100, -100)
sleep(2000)
svol(voice, 100, 100)
sleep(2000)
sstop()
print("PANNED")
'''


def test_knocks_pan(t):
    # splay's pan and svol, recorded: the alarm on the left speaker only, then the right only
    recording = t.path('pan.wav')
    with open(t.path('pan.aer'), 'w') as f:
        f.write(PAN)
    files = {'/bin/pan.aer': t.path('pan.aer'), '/bin/alarm.wav': t.repo('packages', 'knocks', 'alarm.wav')}
    aeros = t.boot(sound=recording, disk=t.disk(files=files))
    aeros.first_login()
    aeros.run('pan', 2)
    aeros.wait_for('PANNED', 30)
    aeros.quit()
    with wave.open(recording) as w:
        rate, channels, count = w.getframerate(), w.getnchannels(), w.getnframes()
        samples = struct.unpack(f'<{count * channels}h', w.readframes(count))
    if channels != 2:
        aeros.fail(f'the recording has {channels} channels')
    left, right = samples[0::2], samples[1::2]
    # tenths of a second with sound, and which side each was on
    sides = []
    for start in range(0, len(left) - rate // 10, rate // 10):
        l = sum(abs(s) for s in left[start:start + rate // 10])
        r = sum(abs(s) for s in right[start:start + rate // 10])
        if l + r > 1000 * rate // 10:
            sides.append('L' if l > r * 20 else 'R' if r > l * 20 else '?')
    heard = ''.join(sides)
    if 'LLLLLLLLLL' not in heard or 'RRRRRRRRRR' not in heard or heard.find('L') > heard.find('R'):
        aeros.fail(f'the alarm was not heard on the left, then the right: {heard}')


def test_knocks_scan(t):
    # SCAN finds nothing suspicious in the game
    aeros = t.boot(disk=t.disk(size_mb=48, files=t.package('knocks')))
    aeros.first_login()
    aeros.run('scan knocks')
    aeros.expect_printed('/apps/knocks.aer can:', 'Nothing looks suspicious.')


def test_knocks_leave(t):
    # The other ending, played by the demo from B2: the power on, then the lift up and away,
    # and three nights later, the telephone
    aeros = boot_with_knocks(t)
    aeros.run('knocks demo 2 leave', 3)
    start = time.time()
    shot = 0
    while 'Thanks for playing Four Knocks.' not in aeros.serial():
        if time.time() - start > 400:
            aeros.fail('the demo did not reach the second ending')
        if 'Error' in aeros.serial():
            aeros.fail('an error in the game')
        keep(aeros, f'leave{shot:02d}')
        shot += 1
        time.sleep(5)
    aeros.expect_printed('Four Knocks: THE END, ENDING 2 OF 2:  LEFT OPEN')
    aeros.expect_not('Error')
