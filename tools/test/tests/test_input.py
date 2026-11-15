"""The keyboard and the mouse, as AerLang programs see them."""

import time

from aeros import GUEST_PROMPT


def test_mouse(t):
    # One line at the prompt prints the mouse every 200 ms for 6 seconds, while QEMU moves it and
    # presses the left button, each step waiting until a line shows it arrived. (VGA text mode
    # has no pixels, so the pointer moves over a 640 x 480 area, starting in the middle)
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.ask('for i = 1 to 30 do print(mouse(), mousemove(), mousedown(MOUSE_LEFT), sleep(200)) end')
    aeros.wait_for('[320, 240, 0] [0, 0] false', 10)    # the mouse is on, and hasn't moved
    aeros.mouse_move(40, 0)
    aeros.wait_for('[360, 240, 0]', 10)                 # 40 to the right
    aeros.mouse_move(0, 25)
    aeros.wait_for('[360, 265, 0]', 10)                 # 25 down (the screen counts down)
    aeros.mouse_buttons(1)
    aeros.wait_for('[360, 265, 1] [0, 0] true', 10)     # the left button held
    aeros.mouse_buttons(0)
    # mousemove() gave each movement once, in the line after it happened
    aeros.expect_printed('[360, 240, 0] [40, 0]', '[360, 265, 0] [0, 25]')
    aeros.wait_for(GUEST_PROMPT, 15)


def test_mouse_is_off_after_a_program(t):
    # the mouse is only for programs: once the line has finished, moving it changes nothing, and
    # the keyboard still works (mouse bytes never turn into key presses)
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.run('mouse()')
    aeros.mouse_move(100, 100)
    aeros.mouse_buttons(1)
    aeros.mouse_buttons(0)
    time.sleep(0.5)
    aeros.cls()
    aeros.run('print("keys still work")')
    aeros.run('mouse()')
    aeros.expect_line('keys still work', '[320, 240, 0]')


def test_keys_held(t):
    # keydown() sees a key while it is held, without waiting. The loop looks for about 5
    # seconds (1000 times, sleeping 5 ms each time keydown is false) while Q is held for one.
    # QEMU presses keys in order, so Q goes down straight after the Enter that starts the loop
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.run('let seen = false')
    before = aeros.prompts()
    aeros.type('for i = 1 to 1000 do if keydown("q") or sleep(5) then seen = true end end\n')
    aeros.key('q', 1000)
    aeros.wait_prompt(before, 30)
    # the held Q also typed some q's, which wait at the prompt: rub them out
    for _ in range(30):
        aeros.key('backspace')
    aeros.cls()
    aeros.run('seen')
    aeros.expect_line('true')


def highlighted_cells(aeros):
    """The cells (row, column) shown with a light background: in VGA text mode, the terminal's
    mouse pointer is the cell under it with its colours swapped (light grey text on black becomes
    black on light grey). Nothing else at a plain prompt has a background."""
    colours = aeros.screen_colours()
    return [(i // 80, i % 80) for i, colour in enumerate(colours) if colour >> 4 != 0]


def wait_for_pointer(aeros, cell, timeout=10):
    """Wait until the pointer is the one highlighted cell, at (row, column)."""
    start = time.time()
    while highlighted_cells(aeros) != [cell]:
        if time.time() - start > timeout:
            aeros.fail(f'expected the pointer at {cell}, but the highlighted cells are {highlighted_cells(aeros)}')
        time.sleep(0.1)


def test_pointer_at_the_prompt(t):
    # While the prompt waits, the mouse shows as a pointer: in text mode, a highlighted cell. The
    # grid is 640 x 400 of the pointer's pixels, 8 x 16 to a cell, and it starts in the middle
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    wait_for_pointer(aeros, (12, 40))              # pixel 320, 200
    aeros.mouse_move(80, 0)
    wait_for_pointer(aeros, (12, 50))              # 80 pixels = 10 cells to the right
    aeros.mouse_move(0, 48)
    wait_for_pointer(aeros, (15, 50))              # 48 pixels = 3 rows down

    # while a line runs the pointer is gone and the mouse is the program's, from the middle of
    # its own area as always; back at the prompt the pointer is where it was
    aeros.cls()
    aeros.run('mouse()')
    aeros.expect_line('[320, 240, 0]')
    wait_for_pointer(aeros, (15, 50))


def test_pointer_in_graphics_mode(t):
    # On a framebuffer the pointer is an arrow, and what is under it is kept: moved over the big
    # title and back again, the screen is exactly as it was
    aeros = t.boot(graphics=True)
    aeros.wait_for(GUEST_PROMPT, 60)
    time.sleep(1)
    before = aeros.screenshot('before')
    aeros.mouse_move(-150, -150)                   # up and left, onto the "aerOS" title
    time.sleep(1)
    over = aeros.screenshot('over_title')
    if over[2] == before[2]:
        aeros.fail('moving the mouse did not move the pointer')
    aeros.mouse_move(150, 150)                     # back where it started
    time.sleep(1)
    if aeros.screenshot('back')[2] != before[2]:
        aeros.fail('the screen is not as it was after the pointer moved over the title and back')


# ---- the mouse at the prompt: selecting, copying, pasting, clicking and the wheel ----------------
# In text mode the pointer moves over 640 x 400 of its own pixels: a cell is 8 across and 16 down.
# QEMU moves it by however much it is told, so the tests keep track of which cell it is on

class Pointer:
    """Where the pointer is, as (row, column), and a way to move it to another cell."""

    def __init__(self, aeros, row=12, col=40):
        self.aeros = aeros
        self.row, self.col = row, col          # it starts in the middle of the screen

    def to(self, row, col):
        self.aeros.mouse_move((col - self.col) * 8, (row - self.row) * 16)
        self.row, self.col = row, col
        time.sleep(0.3)

    def click(self, button=1):
        self.aeros.mouse_buttons(button)
        self.aeros.mouse_buttons(0)
        time.sleep(0.3)


def selected_cells(aeros):
    """The cells shown as selected: black text on cyan (3), or, for the one cell the pointer is on
    (which has its colours swapped), cyan text on black."""
    return [(i // 80, i % 80) for i, colour in enumerate(aeros.screen_colours())
            if colour >> 4 == 3 or colour == 0x03]


def test_select_copy_and_paste(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('ech hello world')                  # row 1 says "hello world"
    pointer = Pointer(aeros)

    # drag across "hello": selected while the button is held, copied when it comes up
    pointer.to(1, 0)
    aeros.mouse_buttons(1)
    pointer.to(1, 4)
    if selected_cells(aeros) != [(1, c) for c in range(5)]:
        aeros.fail(f'dragging over "hello" selected {selected_cells(aeros)}')
    aeros.mouse_buttons(0)
    time.sleep(0.3)
    aeros.cls()
    aeros.run('clip()')
    aeros.expect_line('hello')

    # a right click pastes it into the line being typed, where the cursor is
    aeros.cls()
    aeros.type('ech ')
    aeros.wait_for('ech ')
    pointer.click(2)
    aeros.wait_for('ech hello')
    before = aeros.prompts()
    aeros.type('\n')
    aeros.wait_prompt(before)
    aeros.expect_line('hello')

    # and so does Ctrl+V. A line break in the clipboard becomes a space, so it can't run a line
    aeros.cls()
    aeros.run('setclip("two" + chr(10) + "lines")')
    aeros.type('ech ')
    aeros.wait_for('ech ')
    aeros.key('ctrl-v')
    aeros.wait_for('ech two lines')
    before = aeros.prompts()
    aeros.type('\n')
    aeros.wait_prompt(before)
    aeros.expect_line('two lines')


def test_double_click_and_placing_the_cursor(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('ech one two three')                # row 1: "two" is columns 4 to 6
    pointer = Pointer(aeros)

    # a double click selects and copies the word under the pointer
    pointer.to(1, 5)
    aeros.mouse_buttons(1)
    aeros.mouse_buttons(0)
    aeros.mouse_buttons(1)
    aeros.mouse_buttons(0)
    time.sleep(0.5)
    if selected_cells(aeros) != [(1, 4), (1, 5), (1, 6)]:
        aeros.fail(f'a double click on "two" selected {selected_cells(aeros)}')
    aeros.cls()
    aeros.run('clip()')
    aeros.expect_line('two')

    # a click on the line being typed puts the cursor there: after the prompt (11 characters,
    # "guest@0//: "), "ech abc" has its b in column 16. Clicking there and typing X puts the X
    # before the b
    aeros.cls()
    aeros.type('ech abc')
    aeros.wait_for('ech abc')
    pointer.to(0, 16)
    pointer.click()
    aeros.type('X')
    aeros.wait_for('ech aXbc')
    before = aeros.prompts()
    aeros.type('\n')
    aeros.wait_prompt(before)
    aeros.expect_line('aXbc')


def test_the_wheel_scrolls_back(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('for i = 1 to 60 do print("line " + str(i)) end')
    # the screen shows the end of it; each notch up looks 3 lines further back
    if 'line 30' in aeros.screen():
        aeros.fail('line 30 should have scrolled off the top')
    for _ in range(5):
        aeros.mouse_wheel(-1)
        time.sleep(0.2)
    aeros.wait_for('line 30', 10)
    # and down again comes back to the live screen, with the prompt
    for _ in range(10):
        aeros.mouse_wheel(1)
        time.sleep(0.2)
    aeros.wait_gone('line 30', 10)
    aeros.expect(GUEST_PROMPT)
