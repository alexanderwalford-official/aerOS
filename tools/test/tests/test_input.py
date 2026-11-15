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
