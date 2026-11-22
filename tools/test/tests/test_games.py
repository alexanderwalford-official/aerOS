"""The games, booted from the ISO so GRUB sets up a graphics screen. They are packages (in the
repository's packages/ folder), so each test puts its game on the disk the way pkg install
would (t.package), rather than fetching it from a package server.

In graphics mode the text screen can't be read, so these check the serial log (where the
terminal's text also goes) and the pixels of screenshots."""

import struct
import time
import wave


def colours(shot):
    """How many different colours a screenshot has (a sample of its pixels, for speed)."""
    width, height, pixels = shot
    step = 3 * 97   # every 97th pixel
    return len({pixels[i:i + 3] for i in range(0, len(pixels) - 2, step)})


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


def wait_for_colour(aeros, colour, name, timeout=60):
    """Take screenshots until one has this colour in it."""
    start = time.time()
    while not has_colour(aeros.screenshot(name), colour):
        if time.time() - start > timeout:
            aeros.fail(f'{name}: waited {timeout}s for the colour {colour:06X}')
        time.sleep(0.5)


def boot_with(t, game, sound=False):
    """Boot from the ISO with a game installed, and log in."""
    aeros = t.boot(graphics=True, sound=sound, disk=t.disk(files=t.package(game)))
    aeros.first_login()
    return aeros


def test_maze3d(t):
    aeros = boot_with(t, 'maze3d')
    aeros.run('maze3d', 5)
    if colours(aeros.screenshot('title')) < 3:
        aeros.fail('the title screen looks empty')

    # start, then look round with the mouse: the picture must change
    aeros.key('spc')
    time.sleep(3)
    before = aeros.screenshot('start')
    if colours(before) < 6:
        aeros.fail('the maze looks empty')
    for _ in range(10):
        aeros.mouse_move(40, 0)
        time.sleep(0.05)
    time.sleep(1)
    after = aeros.screenshot('turned')
    if before[2] == after[2]:
        aeros.fail('moving the mouse did not turn the view')

    # walking forwards changes it too
    aeros.key('up', 800)
    time.sleep(1.5)
    if aeros.screenshot('walked')[2] == after[2]:
        aeros.fail('holding Up did not move')

    quit_maze(aeros)


def quit_maze(aeros):
    """Escape from a game of Maze 3D: the first goes back to its menu, the second quits."""
    aeros.key('esc')
    time.sleep(1)
    aeros.key('esc')
    aeros.wait_for('Thanks for playing Maze 3D!', 20)
    aeros.expect_not('Error')


def choose(aeros, keys):
    """Press keys one at a time, e.g. to make choices on Maze 3D's menu."""
    for key in keys:
        aeros.key(key)
        time.sleep(0.3)


# maze3d.aer's colours for the eyes of a ghost chasing you, and for the "CAUGHT" screen
GHOST_STARE = 0xFF3355
CAUGHT = 0xFF5A5F


def boot_with_maze(t, changes, sound=None):
    """Boot with a copy of maze3d.aer changed by (old line, new line) pairs, installed as maze3d
    (with its sounds). sound is t.boot's: a file name records what the sound card plays."""
    game = open(t.repo('packages', 'maze3d.aer')).read()
    for old, new in changes:
        if game.count(old) != 1:
            raise AssertionError(f'maze3d.aer no longer has the line {old.strip()!r}: update this test')
        game = game.replace(old, new)
    with open(t.path('maze3d.aer'), 'w') as f:
        f.write(game)
    files = t.package('maze3d')
    files['/apps/maze3d.aer'] = t.path('maze3d.aer')
    aeros = t.boot(graphics=True, sound=sound, disk=t.disk(files=files))
    aeros.first_login()
    return aeros


# A ghost two squares in front of where you start, looking straight at you
GHOST_AHEAD = ('    "#P...#.....o..X#",\n', '    "#P.X.#.....o..X#",\n')


def test_maze3d_ghosts(t):
    # Ghosts that stand still, one of them just ahead: it is drawn (with red eyes, as it is
    # near enough to chase you), and the light sends it away
    aeros = boot_with_maze(t, [GHOST_AHEAD,
                               ('let GHOST_SPEED = 1.2 ', 'let GHOST_SPEED = 0.0 '),
                               ('let CHASE_SPEED = 1.9 ', 'let CHASE_SPEED = 0.0 ')])
    aeros.run('maze3d', 5)
    aeros.key('spc')
    wait_for_colour(aeros, GHOST_STARE, 'ghost', timeout=20)
    aeros.key('spc')
    time.sleep(1.5)
    if has_colour(aeros.screenshot('light'), GHOST_STARE):
        aeros.fail('shining the light at the ghost did not send it away')
    quit_maze(aeros)


def test_maze3d_caught(t):
    # With one life (chosen on the menu: down to LIVES, then left from 3 to 1), the ghost just
    # ahead comes for you, and that's the game over
    aeros = boot_with_maze(t, [GHOST_AHEAD])
    aeros.run('maze3d', 5)
    choose(aeros, ['down', 'down', 'down', 'left', 'spc'])
    wait_for_colour(aeros, CAUGHT, 'caught', timeout=30)
    aeros.key('esc')
    aeros.wait_for('Thanks for playing Maze 3D!', 20)
    aeros.expect_not('Error')


def test_maze3d_huge(t):
    # The biggest made-up maze, with lots of crystals and many ghosts: it is made, drawn, and
    # can be looked round. (It is made up afresh each time, so it is only checked loosely.)
    # On the menu, left from CLASSIC goes round to HUGE, then crystals and ghosts go up one each
    aeros = boot_with(t, 'maze3d')
    aeros.run('maze3d', 5)
    choose(aeros, ['left', 'down', 'right', 'down', 'right', 'spc'])
    time.sleep(8)
    before = aeros.screenshot('huge')
    if colours(before) < 6:
        aeros.fail('the huge maze looks empty')
    aeros.key('left', 600)
    time.sleep(1.5)
    if aeros.screenshot('turned')[2] == before[2]:
        aeros.fail('holding Left did not turn in the huge maze')
    quit_maze(aeros)


def loud_quarters(path):
    """For each quarter second of a recording QEMU made: None if it is quiet, or else its pitch
    in Hz, from how often the wave crosses zero. (Like notes_in in test_media.py, but keeping
    the quiet parts, so a test can see when things were heard)"""
    with wave.open(path) as w:
        rate, channels, count = w.getframerate(), w.getnchannels(), w.getnframes()
        samples = struct.unpack(f'<{count * channels}h', w.readframes(count))[0::channels]
    quarters = []
    window = rate // 4
    for start in range(0, len(samples) - window + 1, window):
        part = samples[start:start + window]
        loudness = (sum(s * s for s in part) / len(part)) ** 0.5
        if loudness < 300:
            quarters.append(None)
            continue
        crossings = sum(1 for a, b in zip(part, part[1:]) if (a < 0) != (b < 0))
        quarters.append(crossings / 2 / (len(part) / rate))
    return quarters


CRYSTAL_BELL = 2093   # the note (C7) crystal.wav rings on (see tools/make_maze3d_sounds.py)


def test_maze3d_sound(t):
    # With a sound card, recorded: the menu's music plays, and taking a crystal rings its bell.
    # A crystal is put next to the start, and can be taken from there, so it is taken on the
    # very first frame. The game is played with SOUND on EFFECTS ONLY (no music), and the
    # ghosts stand still, so after the bell all is quiet.
    recording = t.path('out.wav')
    aeros = boot_with_maze(t, [('    "#P...#.....o..X#",\n', '    "#Po..#.....o..X#",\n'),
                               ('let PICK_UP_DISTANCE = 0.5 ', 'let PICK_UP_DISTANCE = 1.2 '),
                               ('let GHOST_SPEED = 1.2 ', 'let GHOST_SPEED = 0.0 '),
                               ('let CHASE_SPEED = 1.9 ', 'let CHASE_SPEED = 0.0 ')],
                           sound=recording)
    aeros.run('maze3d', 6)
    # down to SOUND, right to EFFECTS ONLY, and start
    choose(aeros, ['down', 'down', 'down', 'down', 'right', 'spc'])
    time.sleep(5)
    quit_maze(aeros)
    aeros.quit()

    quarters = loud_quarters(recording)
    bell = [i for i, hz in enumerate(quarters) if hz and abs(hz - CRYSTAL_BELL) <= CRYSTAL_BELL * 0.03]
    if not bell:
        aeros.fail(f'the crystal\'s bell ({CRYSTAL_BELL} Hz) was not heard: {quarters}')
    at = bell[0]
    # the 3 seconds before it (but the last half second: the start sound) were the menu's music
    menu = quarters[max(0, at - 14):at - 2]
    if sum(1 for q in menu if q) < 10:
        aeros.fail(f'the menu\'s music was not heard before the game: {menu}')
    # from 1.5 seconds after it, for 2 seconds: nothing (no music, with EFFECTS ONLY)
    after = quarters[at + 6:at + 14]
    if any(after):
        aeros.fail(f'there was sound after the bell, with only effects on: {after}')


def test_move(t):
    # move.aer: a square that moves while an arrow key is held, drawn with a back buffer
    aeros = boot_with(t, 'move', sound=True)
    aeros.ask('move', 3)
    first = aeros.screenshot('start')
    aeros.key('right', 1000)
    time.sleep(1.5)
    if aeros.screenshot('moved')[2] == first[2]:
        aeros.fail('holding Right did not move the square')
    before = aeros.prompts()
    aeros.key('esc')
    aeros.wait_prompt(before, 20)
    aeros.expect_not('Error')


def test_snake(t):
    aeros = boot_with(t, 'snake')
    # Snake asks first if it may keep its high scores in its own folder, /apps/snake/
    aeros.ask('snake')
    aeros.wait_for('[ ? ]', 30)
    aeros.key('y')
    time.sleep(4)
    aeros.key('spc')
    time.sleep(0.5)
    first = aeros.screenshot('playing')
    time.sleep(0.5)
    # the snake moves by itself, so two pictures apart differ. (Quickly: it reaches the
    # wall in about 3 seconds, and Escape then needs pressing again on the game-over screen)
    if aeros.screenshot('moved')[2] == first[2]:
        aeros.fail('the snake did not move')
    aeros.key('esc')
    aeros.wait_for('Thanks for playing Snake!', 20)
    aeros.expect_not('Error')


GOLD = 0xFFD166   # Snake's colour for "NEW HIGH SCORE" and the high scores' heading


def test_snake_high_scores(t):
    # A copy of Snake whose score starts at 7, so the snake running into the wall by itself
    # makes a high score, and asks for a name
    game = open(t.repo('packages', 'snake.aer')).read()
    if game.count('    score = 0\n') != 1:
        raise AssertionError("snake.aer's score = 0 line has changed: update this test")
    with open(t.path('snake7.aer'), 'w') as f:
        f.write(game.replace('    score = 0\n', '    score = 7\n'))
    # installed as snake, in place of the real one
    files = t.package('snake')
    files['/apps/snake.aer'] = t.path('snake7.aer')
    disk = t.disk(files=files)

    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    before = aeros.prompts()
    aeros.ask('snake')
    aeros.wait_for('[ ? ]', 30)
    aeros.key('a')
    time.sleep(4)
    # no scores yet, so the title screen has no high scores on it
    if has_colour(aeros.screenshot('title'), GOLD):
        aeros.fail('the title screen shows high scores before there are any')

    # play: the snake goes right until it hits the wall, and then it's a high score
    aeros.key('spc')
    wait_for_colour(aeros, GOLD, 'new_high_score')
    aeros.type('Ann\n')
    time.sleep(2)
    aeros.screenshot('game_over')
    aeros.key('esc')
    aeros.wait_prompt(before, 20, what='snake')
    aeros.expect_not('Error')

    # the score is in the database, in Snake's own folder
    aeros.cls()
    aeros.run('sql /apps/snake/scores.db SELECT game, name, score FROM scores')
    aeros.expect_line('game | name | score', 'snake | Ann | 7', '(1 row)')

    # and on the title screen next time, without asking again (the serial log keeps
    # everything, so count the questions)
    asked = aeros.serial().count('[ ? ]')
    aeros.ask('snake')
    time.sleep(4)
    if not has_colour(aeros.screenshot('title_with_scores'), GOLD):
        aeros.fail('the title screen does not show the high scores')
    if aeros.serial().count('[ ? ]') != asked:
        aeros.fail('Snake asked again, after being told "always"')
