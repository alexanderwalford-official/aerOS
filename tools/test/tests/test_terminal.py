"""The terminal itself: usage messages, Tab completion, scrolling back, the editor, and how the
screen shows a line as wide as itself and the big title as it scrolls."""

import time

from aeros import GUEST_PROMPT


def test_usage_messages(t):
    # a command typed without what it needs says how to use it
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    for command in ['rd', 'mkf', 'del', 'sd']:
        aeros.run(command)
    aeros.expect('Usage: RD', 'Usage: MKF', 'Usage: DEL', 'Usage: SD')


def test_tab_completion(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    # Tab fills in the rest of a file's name, so Enter reads /bin/hello.aer
    aeros.type('rd /bin/hel')
    aeros.key('tab')
    aeros.wait_for('rd /bin/hello.aer')
    aeros.run('')
    # hello.aer is longer than the screen, so look in everything that was printed
    aeros.expect_printed('rd /bin/hello.aer', 'run it from the terminal by typing: hello')


def test_scrollback(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('for i = 1 to 60 do print("line " + str(i)) end')
    aeros.expect_line('line 60')

    # Page Up looks back a screen at a time, and says how far back it is
    aeros.key('pgup')
    aeros.wait_for('24 lines back')
    aeros.expect('PgUp / PgDn')
    aeros.expect_not('line 60')
    aeros.key('pgdn')
    aeros.wait_gone('lines back')

    # typing while looking back goes back to the live screen first
    aeros.key('pgup')
    aeros.wait_for('24 lines back')
    aeros.run('print("typed while scrolled")')
    aeros.expect_not('lines back')
    aeros.expect_line('typed while scrolled')


def test_editor(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.ask('edt t.aer')
    aeros.wait_for('^S Save')
    aeros.type('let x = 5\nprint("x is", x)')
    aeros.key('ctrl-s')
    aeros.wait_for('Saved 26 bytes')
    aeros.expect('^R Run')

    # Ctrl+R runs it from the editor
    aeros.key('ctrl-r')
    aeros.wait_for('x is 5', 20)
    aeros.key('ret')
    time.sleep(0.5)

    # Ctrl+Q leaves, and the file is saved as typed
    before = aeros.prompts()
    aeros.key('ctrl-q')
    aeros.wait_prompt(before)
    aeros.cls()
    aeros.run('rd t.aer')
    aeros.expect_line('let x = 5', 'print("x is", x)')


def test_editing_a_line(t):
    # Left, Right, Home and End move along the line; typing goes in at the cursor, Backspace
    # takes the character before it and Delete the one under it
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()

    def keys(*names):
        for name in names:
            aeros.key(name)

    # "ec hello" with an h put in after the "ec"
    aeros.type('ec hello')
    keys('home', 'right', 'right')
    aeros.type('h')
    aeros.run('')
    # "eccch hi" with two of the c's taken out by Backspace
    aeros.type('eccch hi')
    keys('home', 'right', 'right', 'right', 'right', 'backspace', 'backspace')
    aeros.run('')
    # "exch yes" with the x taken out by Delete
    aeros.type('exch yes')
    keys('home', 'right', 'delete')
    aeros.run('')
    aeros.expect_line('hello', 'hi', 'yes')

    # a line long enough to wrap onto the next row, edited before the wrap
    aeros.cls()
    aeros.type('ech ' + 'a' * 60 + 'B' * 30)
    keys(*(['left'] * 40))
    aeros.type('XYZ')
    aeros.run('')
    aeros.expect_printed('a' * 50 + 'XYZ' + 'a' * 10 + 'B' * 30)


def test_a_line_as_wide_as_the_screen(t):
    # A line of exactly 80 characters fills the row, and the cursor goes on to the next one by
    # itself: the newline after it mustn't move it down again, leaving a blank row
    # (console_wrapped in src/drivers/video/VGA_linear.c)
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('let s = ""')
    aeros.run('for i = 1 to 80 do s = s + "x" end')
    aeros.cls()
    aeros.run('print(s + chr(10) + "after")')
    rows = [row.rstrip() for row in aeros.screen().split('\n')]
    if 'x' * 80 not in rows:
        aeros.fail('the 80 character line is not on the screen')
    below = rows[rows.index('x' * 80) + 1]
    if below != 'after':
        aeros.fail(f'the row under an 80 character line is {below!r}, not "after"')
    # an 81st character still wraps onto a row of its own
    aeros.cls()
    aeros.run('print(s + "y" + chr(10) + "after")')
    rows = [row.rstrip() for row in aeros.screen().split('\n')]
    at = rows.index('x' * 80)
    if rows[at + 1] != 'y' or rows[at + 2] != 'after':
        aeros.fail(f'81 characters came out as {rows[at:at + 3]!r}')


ORANGE = bytes([0xFF, 0x7F, 0x00])   # the title's colour on a framebuffer (ORANGE_RGB)


def orange_pixels(shot):
    """Where a screenshot has the title's orange: a set of (x, y)."""
    width, _, pixels = shot
    found = set()
    at = pixels.find(ORANGE)
    while at != -1:
        if at % 3 == 0:
            found.add(((at // 3) % width, (at // 3) // width))
        at = pixels.find(ORANGE, at + 1)
    return found


def test_the_title_scrolls_with_the_text(t):
    # The big "aerOS" at the top is drawn in pixels over blank cells (and "Version ..." under it
    # is orange text). As the text scrolls, all of that orange moves up together, the same
    # distance, with nothing left where it was. (Before, the title's blank cells kept their
    # pixels while text moving through them didn't, leaving pieces of it behind)
    aeros = t.boot(graphics=True)
    aeros.wait_for(GUEST_PROMPT, 60)
    time.sleep(1)
    before = orange_pixels(aeros.screenshot('title'))
    if len(before) < 1000:
        aeros.fail(f'the title is not on the screen ({len(before)} orange pixels)')
    # a few lines: enough to scroll the screen, but not the bottom of the title off it
    aeros.run('for i = 1 to 12 do print(i) end')
    time.sleep(1)
    after = orange_pixels(aeros.screenshot('moved'))
    if not after:
        aeros.fail('after a little scrolling there is no orange left')
    # how far it went up: the bottom of the orange (the "Version" line) is still on the screen
    moved = max(y for _, y in before) - max(y for _, y in after)
    if moved <= 0:
        aeros.fail('the screen did not scroll')
    stray = [(x, y) for x, y in after if (x, y + moved) not in before]
    if stray:
        aeros.fail(f'{len(stray)} orange pixels are not where the title moved to, e.g. {stray[:3]}')
    # and all of it is there, but what went off the top
    top = min(y for _, y in after)
    missing = [(x, y) for x, y in before if y - moved > top and (x, y - moved) not in after]
    if missing:
        aeros.fail(f'{len(missing)} pixels of the title did not come with it, e.g. {missing[:3]}')
    # far enough for it all to go off the top: nothing orange is left behind
    aeros.run('for i = 1 to 40 do print(i) end')
    time.sleep(1)
    left = orange_pixels(aeros.screenshot('gone'))
    if left:
        aeros.fail(f'{len(left)} orange pixels were left behind after the title scrolled away')
