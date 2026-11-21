"""The desktop package (packages/desktop.aer): windows, the taskbar and start menu, the mouse and
the keyboard, and Notes saving what was typed. Booted from the ISO, so there is a graphics
screen; the desktop is put on the disk the way pkg install would (t.package)."""

import time

from aeros import read_disk_file

TITLE_ON = bytes([0x2B, 0x5D, 0x8C])    # the front window's title bar (desktop.aer)
MENU = bytes([0x26, 0x26, 0x2E])        # the start menu
SKY = bytes([0x1B, 0x3A, 0x5C])         # the top of the wallpaper
# aerOS's own question, in a box over the graphics: its "Y = yes ..." line (ask_in_graphics in
# src/sys/perm.c). It isn't on the serial port, as a question at the prompt is
QUESTION = bytes([0xFF, 0xD0, 0x80])


def has(shot, colour):
    """Whether a screenshot has a pixel of this colour (3 bytes: red, green, blue)."""
    pixels = shot[2]
    at = pixels.find(colour)
    while at != -1:
        if at % 3 == 0:
            return True
        at = pixels.find(colour, at + 1)
    return False


def wait_for_colour(aeros, colour, what, timeout=30):
    """Take screenshots until one has this colour."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if has(aeros.screenshot('desktop'), colour):
            return
        time.sleep(0.3)
    aeros.fail(f'waited {timeout}s for {what}')


def test_desktop(t):
    disk = t.disk(files=t.package('desktop'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    before = aeros.prompts()
    aeros.ask('desktop')

    # the wallpaper, and the clock's window in front
    wait_for_colour(aeros, SKY, 'the wallpaper', 60)
    wait_for_colour(aeros, TITLE_ON, 'the clock window')

    # the mouse: into the bottom left corner (as far as it goes: QEMU sends a big move in
    # pieces of a few hundred pixels at most, so it is moved a step at a time), then onto the
    # start button
    for _ in range(8):
        aeros.mouse_move(-200, 200)
        time.sleep(0.1)
    time.sleep(0.5)
    aeros.mouse_move(40, -17)
    time.sleep(0.5)
    aeros.mouse_buttons(1)
    time.sleep(0.2)
    aeros.mouse_buttons(0)
    wait_for_colour(aeros, MENU, 'the start menu, after clicking the start button')
    aeros.key('esc')
    time.sleep(0.5)

    # the keyboard: Tab opens the menu, Down twice is Notes. It reads notes.txt in the home
    # folder, so aerOS asks first
    aeros.key('tab')
    time.sleep(0.4)
    aeros.key('down')
    aeros.key('down')
    time.sleep(0.4)
    aeros.key('ret')
    answer_question(aeros)
    aeros.type('Written on the desktop')
    time.sleep(1)

    # Esc closes Notes, which saves it first (aerOS asks again: this time to change the file)
    aeros.key('esc')
    answer_question(aeros)
    # Esc closes the clock, and with nothing left open, Esc leaves the desktop
    aeros.key('esc')
    time.sleep(0.5)
    aeros.key('esc')
    aeros.wait_prompt(before, 30, what='desktop')
    aeros.expect('Left the desktop.')
    aeros.expect_not('Error')
    aeros.quit()
    if read_disk_file(disk, '/home/tester/notes.txt') != b'Written on the desktop':
        aeros.fail('Notes did not save what was typed in /home/tester/notes.txt')


def answer_question(aeros, timeout=30):
    """Wait for aerOS's question box, answer A (always), and wait for it to go."""
    wait_for_colour(aeros, QUESTION, "aerOS's question, before the file was used", timeout)
    aeros.key('a')
    deadline = time.time() + timeout
    while has(aeros.screenshot('desktop'), QUESTION):
        if time.time() > deadline:
            aeros.fail('the question stayed after it was answered')
        time.sleep(0.3)
