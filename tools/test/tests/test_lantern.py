"""Lantern (packages/lantern.aer), the procedural adventure: booted from the ISO so GRUB sets up
a graphics screen, with the game installed the way pkg install would (t.package).

The game reads its arguments for testing: "lantern world 1 2" only makes those worlds and prints
a checksum of each, its story and whether every place is reachable; "lantern seed 7" starts a
new game in world 7 straight away; "lantern demo 7" plays a game in world 7 by itself to an
ending ("mercy" or "join" for those endings, "fast" for minute-long days), printing what
happens. In graphics mode the text screen can't be read, so these look at the serial log
(where the game's print()s and any error go) and at screenshots' pixels."""

import os
import re
import time

# Where to keep pictures of the game, for a person to look at (set LANTERN_SHOTS to a folder)
SHOTS = os.environ.get('LANTERN_SHOTS')


def colours(shot):
    """How many different colours a screenshot has (a sample of its pixels, for speed)."""
    width, height, pixels = shot
    step = 3 * 97
    return len({pixels[i:i + 3] for i in range(0, len(pixels) - 2, step)})


def keep(aeros, name):
    """Take a screenshot, keep a PNG of it in SHOTS (if set), and give it back."""
    shot = aeros.screenshot(name)
    if SHOTS:
        from PIL import Image
        width, height, pixels = shot
        Image.frombytes('RGB', (width, height), pixels).save(os.path.join(SHOTS, f'lantern-{name}.png'))
    return shot


def boot_with_lantern(t, sound=None):
    aeros = t.boot(graphics=True, sound=sound, disk=t.disk(size_mb=48, files=t.package('lantern')))
    aeros.first_login()
    return aeros


def wait_or_error(aeros, text, timeout):
    """Wait for the game to print text, failing at once if it stops with an error instead."""
    start = time.time()
    while text not in aeros.serial():
        if 'Error' in aeros.serial():
            error = [line for line in aeros.serial().splitlines() if 'Error' in line]
            aeros.fail('an error in the game: ' + error[0])
        if time.time() - start > timeout:
            aeros.fail(f'waited {timeout}s for "{text}"')
        time.sleep(0.5)


def no_error(aeros):
    if 'Error' in aeros.serial():
        aeros.fail('an error in the game')


def test_lantern_worlds(t):
    # The same seed makes the same world (every tile, and the story); different seeds make
    # different ones; and in every world each place the story needs can be walked to
    aeros = boot_with_lantern(t)
    seeds = [1, 2, 3, 77, 4242, 99991, 123456, 31337]
    aeros.run('lantern world ' + ' '.join(str(s) for s in seeds + [4242]), 3)
    wait_or_error(aeros, 'worlds done', 300)
    time.sleep(1)
    printed = aeros.serial()
    no_error(aeros)
    sums = re.findall(r'world (\d+): (\d+)', printed)
    if len(sums) != len(seeds) + 1:
        aeros.fail(f'expected {len(seeds) + 1} worlds, got {sums}')
    by_seed = {}
    for seed, checksum in sums:
        if seed in by_seed and by_seed[seed] != checksum:
            aeros.fail(f'seed {seed} made two different worlds: {by_seed[seed]} and {checksum}')
        by_seed[seed] = checksum
    if len(set(by_seed.values())) != len(seeds):
        aeros.fail(f'different seeds made the same world: {by_seed}')
    stories = set(re.findall(r'story \d+: (.*)', printed))
    if len(stories) < len(seeds) - 1:
        aeros.fail(f'the stories are too alike: {stories}')
    missing = re.findall(r'reach (\d+): missing (.*)', printed)
    if missing:
        aeros.fail(f'places that cannot be reached: {missing}')


def test_lantern_start(t):
    # A new game in a fixed world, with a sound card: its pictures and sounds load, the intro
    # is told, and you can walk about the village; then the pause menu quits. (The demo plays
    # without a sound card)
    aeros = boot_with_lantern(t, sound=True)
    aeros.run('lantern seed 4242', 3)
    wait_or_error(aeros, 'lantern: ready', 60)
    if 'sound card true' not in aeros.serial().lower():
        aeros.fail('the sounds did not load (or there is no sound card)')
    wait_or_error(aeros, 'say: : The Vale of', 120)
    time.sleep(2)
    intro = keep(aeros, 'intro')
    if colours(intro) < 12:
        aeros.fail('the intro looks empty: no world behind it')
    for _ in range(3):
        aeros.key('ret')
        time.sleep(0.4)
        aeros.key('ret')
        time.sleep(1.2)
    before = keep(aeros, 'village')
    aeros.key('right', 900)
    time.sleep(1)
    after = keep(aeros, 'walked')
    if before[2] == after[2]:
        aeros.fail('holding Right did not move')
    aeros.key('esc')
    time.sleep(1.5)
    keep(aeros, 'paused')
    for _ in range(5):
        aeros.key('down')
        time.sleep(0.2)
    aeros.key('ret')
    wait_or_error(aeros, 'Thanks for playing Lantern.', 30)
    no_error(aeros)


def play_demo(t, seed, how=''):
    """Let the game play itself in a world to an ending, keeping pictures along the way, and
    give back everything it printed."""
    aeros = boot_with_lantern(t)
    aeros.run(f'lantern demo {seed} {how}', 3)
    start = time.time()
    shot = 0
    next_shot = time.time() + 20
    moments = {'event: quest given': 'quest', 'event: enter cave': 'cave', 'event: villain met': 'villain',
               'event: night': 'night', 'event: raid on': 'raid', 'event: relic found': 'relic',
               'event: rescued': 'rescued'}
    while 'THE END' not in aeros.serial():
        printed = aeros.serial()
        if time.time() - start > 1200:
            done = [line for line in printed.splitlines() if line.startswith(('demo:', 'event:'))]
            aeros.fail('the demo did not reach an ending in 20 minutes. It did:\n' + '\n'.join(done[-30:]))
        if 'Error' in printed:
            error = [line for line in printed.splitlines() if 'Error' in line]
            aeros.fail('an error in the game: ' + error[0])
        for words in [w for w in moments if w in printed]:
            keep(aeros, f'{seed}{how}-' + moments.pop(words))
        if time.time() >= next_shot:
            keep(aeros, f'{seed}{how}-demo{shot:02d}')
            shot += 1
            next_shot = time.time() + 30
        time.sleep(0.3)
    keep(aeros, f'{seed}{how}-ending')
    aeros.wait_for('Thanks for playing Lantern.', 60)
    no_error(aeros)
    return aeros.serial()


def test_lantern_demo(t):
    # A whole game, played by itself in fast days so that nights and raids come round: the
    # quest, the lost one saved (a scripted event, which must fire and be rewarded), the cave,
    # the relic, the lair and the villain, to an ending
    printed = play_demo(t, 4242, 'fast')
    for event in ['event: quest given', 'event: rescued', 'event: lost one rewarded', 'event: sage told',
                  'event: enter cave', 'event: relic found', 'event: gate opened', 'event: villain met',
                  'event: villain beaten', 'event: night', 'ending:']:
        if event not in printed:
            raise AssertionError(f'the demo never got to "{event}"')


def test_lantern_scan(t):
    # What SCAN makes of it before it runs: graphics, sound, its own files (the save), and
    # nothing suspicious
    aeros = t.boot(disk=t.disk(size_mb=48, files=t.package('lantern')))
    aeros.first_login()
    aeros.run('scan lantern')
    aeros.expect_printed('/apps/lantern.aer can:', 'Nothing looks suspicious.')


def test_lantern_mercy(t):
    # Another world, at the normal pace: the demo asks the sage who the villain was, fetches
    # the locket from the ruins, and shows it to the villain instead of fighting
    printed = play_demo(t, 77, 'mercy')
    for event in ['event: sage told', 'event: past told', 'event: locket found', 'event: relic found',
                  'event: villain met', 'ending: The Mended Heart']:
        if event not in printed:
            raise AssertionError(f'the demo never got to "{event}"')
    if 'event: villain beaten' in printed:
        raise AssertionError('the villain was fought, not spared')


def test_lantern_save(t):
    # Save from the pause menu (AerOS asks whether the game may write in /apps/lantern/), quit
    # to the title, and Continue: the same world comes back, from its seed and the save
    from aeros import read_disk_file
    disk = t.disk(size_mb=48, files=t.package('lantern'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    aeros.ask('lantern seed 99991')
    wait_or_error(aeros, 'event: new game, seed 99991', 120)
    for _ in range(4):
        aeros.key('ret')
        time.sleep(0.4)
        aeros.key('ret')
        time.sleep(1.2)
    aeros.key('down', 700)
    time.sleep(1)
    aeros.key('esc')
    time.sleep(1.5)
    for _ in range(3):
        aeros.key('down')
        time.sleep(0.2)
    aeros.key('ret')
    # the question is a box over the picture, which leaves nothing in the serial log
    time.sleep(5)
    keep(aeros, 'may_it_save')
    aeros.key('a')
    wait_or_error(aeros, 'event: saved', 30)
    # quit to the title, which now offers to carry on
    aeros.key('esc')
    time.sleep(1.5)
    for _ in range(4):
        aeros.key('down')
        time.sleep(0.2)
    aeros.key('ret')
    time.sleep(10)
    keep(aeros, 'title')
    aeros.key('down')
    time.sleep(0.3)
    aeros.key('ret')
    wait_or_error(aeros, 'event: loaded', 120)
    time.sleep(2)
    keep(aeros, 'continued')
    aeros.key('esc')
    time.sleep(1.5)
    for _ in range(5):
        aeros.key('down')
        time.sleep(0.2)
    aeros.key('ret')
    wait_or_error(aeros, 'Thanks for playing Lantern.', 30)
    no_error(aeros)
    aeros.quit()
    saved = read_disk_file(disk, '/apps/lantern/save.txt')
    if saved is None or not saved.startswith(b'lantern 1') or b'seed=99991' not in saved:
        aeros.fail(f'the save is {saved!r}')


def test_lantern_join(t):
    # A third world: the demo asks the villain why, and joins them
    printed = play_demo(t, 2, 'join')
    if "ending: The Shadow's Heir" not in printed:
        raise AssertionError("joining the villain did not end in The Shadow's Heir")
