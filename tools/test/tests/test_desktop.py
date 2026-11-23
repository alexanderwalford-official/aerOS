"""The desktop package (packages/desktop.aer): windows, the taskbar and start menu, the mouse and
the keyboard, Notes saving what was typed, the apps found on the disk, the power entries, the
settings and the system monitor. Booted from the ISO, so there is a graphics screen; the
desktop is put on the disk the way pkg install would (t.package)."""

import os
import struct
import time

from aeros import read_disk_file, write_disk_file

TITLE_ON = bytes([0x2B, 0x5D, 0x8C])    # the front window's title bar (desktop.aer)
MENU = bytes([0x26, 0x26, 0x2E])        # the start menu
SKY = bytes([0x1B, 0x3A, 0x5C])         # the top of the wallpaper
FOREST_SKY = bytes([0x1E, 0x3B, 0x2A])  # the top of the wallpaper in the Forest theme
FOREST_TASKBAR = bytes([0x1B, 0x23, 0x1D])
PICTURE = bytes([0x12, 0xA4, 0x56])     # the colour of the picture test_desktop_settings makes
# aerOS's own question, in a box over the graphics: its "Y = yes ..." line (ask_in_graphics in
# src/sys/perm.c). It isn't on the serial port, as a question at the prompt is
QUESTION = bytes([0xFF, 0xD0, 0x80])
# the system monitor (desktop.aer): the network's signal bars while the link is up, the dark
# screen its graphs are drawn on, and the edge round its panel
LINK_UP = bytes([0x4F, 0xD1, 0x8B])
SCREEN_BACK = bytes([0x0A, 0x0D, 0x12])
MONITOR_EDGE = bytes([0x3C, 0x46, 0x54])

# the desktop's own windows, in the order the start menu has them (OWN_APPS in desktop.aer)
OWN_APPS = ['clock', 'files', 'notes', 'calc', 'system', 'settings', 'about', 'browser']


def has(shot, colour):
    """Whether a screenshot has a pixel of this colour (3 bytes: red, green, blue)."""
    pixels = shot[2]
    at = pixels.find(colour)
    while at != -1:
        if at % 3 == 0:
            return True
        at = pixels.find(colour, at + 1)
    return False


def row_has(shot, y, colour):
    """Whether row y of a screenshot has a pixel of this colour."""
    width, _, pixels = shot
    row = pixels[y * width * 3:(y + 1) * width * 3]
    return any(row[i:i + 3] == colour for i in range(0, len(row), 3))


def wait_for_colour(aeros, colour, what, timeout=30, name='desktop'):
    """Take screenshots until one has this colour."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if has(aeros.screenshot(name), colour):
            return
        time.sleep(0.3)
    aeros.fail(f'waited {timeout}s for {what}')


def wait_without_colour(aeros, colour, what, timeout=30):
    """Take screenshots until one hasn't this colour."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not has(aeros.screenshot('desktop'), colour):
            return
        time.sleep(0.3)
    aeros.fail(f'waited {timeout}s for {what}')


def start_desktop(aeros, answer='a'):
    """Start the desktop and answer aerOS's question about reading its settings, in the home
    folder (answer None: don't wait for one, as an "always" was given before)."""
    before = aeros.prompts()
    aeros.ask('desktop')
    wait_for_colour(aeros, SKY, 'the wallpaper', 60)
    if answer:
        answer_question(aeros, answer)
    return before


def menu_entry(aeros, ups=0, downs=0):
    """Open the start menu with Tab, move Up or Down along it, and pick the entry with Enter."""
    aeros.key('tab')
    time.sleep(0.5)
    for _ in range(ups):
        aeros.key('up')
    for _ in range(downs):
        aeros.key('down')
    time.sleep(0.5)
    aeros.key('ret')


def test_desktop(t):
    disk = t.disk(files=t.package('desktop'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    before = aeros.prompts()
    aeros.ask('desktop')

    # the wallpaper, and the clock's window in front
    wait_for_colour(aeros, SKY, 'the wallpaper', 60)
    wait_for_colour(aeros, TITLE_ON, 'the clock window')
    # the settings are in the home folder, so aerOS asks first: A (always) also lets Notes read
    # notes.txt there without asking again
    answer_question(aeros)

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

    # the keyboard: Tab opens the menu, Down twice is Notes
    menu_entry(aeros, downs=2)
    time.sleep(1)
    aeros.type('Written on the desktop')
    time.sleep(1)

    # Esc closes Notes, which saves it first (aerOS asks: this time to change the file)
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


def test_desktop_apps(t):
    """The apps installed are found as the desktop starts: a package from pkg install (guess, in
    /apps/) is in the start menu, after the desktop's own windows and in the order of the
    names, with the apps that come with aerOS (/bin/). Picking it closes the desktop, runs it,
    and brings the desktop back, with the same windows open."""
    files = t.package('desktop')
    files.update(t.package('guess'))
    disk = t.disk(files=files)
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    before = start_desktop(aeros)

    # where Guess is in the menu: after the desktop's own, among every app in /bin/ and /apps/
    # by name, but the desktop and sleep (Sleep is with the power entries)
    apps = sorted([f[:-4] for f in os.listdir(t.repo('src', 'disk', 'bin'))
                   if f.endswith('.aer') and f != 'sleep.aer'] + ['guess'])
    aeros.screenshot('desktop-apps-before')
    menu_entry(aeros, downs=len(OWN_APPS) + apps.index('guess'))

    # the desktop closes, and Guess runs on the text screen. Play it: halve the range each time
    aeros.wait_for('Opening Guess. The desktop comes back when it ends.', 30)
    aeros.wait_for('Your guess: ', 30)
    low, high = 1, 100
    for _ in range(10):
        guess = (low + high) // 2
        seen = len(aeros.serial())
        aeros.type(f'{guess}\n')
        deadline = time.time() + 30
        said = ''
        while time.time() < deadline:
            said = aeros.serial()[seen:]
            if 'Higher!' in said or 'Lower!' in said or 'You got it' in said:
                break
            time.sleep(0.2)
        if 'You got it' in said:
            break
        if 'Higher!' in said:
            low = guess + 1
        elif 'Lower!' in said:
            high = guess - 1
        else:
            aeros.fail(f'Guess said nothing to {guess}')
    aeros.expect('You got it in')

    # a text app: aerOS waits for a key, so what it said last can be read, then the desktop
    # is back, with the clock open again
    aeros.wait_for('Press a key to go back to desktop.aer.', 30)
    aeros.key('spc')
    wait_for_colour(aeros, SKY, 'the desktop again, after Guess', 60)
    wait_for_colour(aeros, TITLE_ON, 'the clock window again')
    aeros.screenshot('desktop-apps-back')
    aeros.key('esc')
    time.sleep(0.5)
    aeros.key('esc')
    aeros.wait_prompt(before, 30, what='desktop')
    aeros.expect('Left the desktop.')
    aeros.expect_not('Error')


def test_desktop_power(t):
    """Sleep darkens the screen until a key; Shut down asks first (no: nothing happens), and
    with a yes the computer turns off (QEMU ends)."""
    disk = t.disk(files=t.package('desktop'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    start_desktop(aeros)
    wait_for_colour(aeros, TITLE_ON, 'the clock window')

    # the start menu ends Sleep, Restart, Shut down, Exit desktop: Up from the first wraps
    # round to the last
    menu_entry(aeros, ups=4)
    wait_without_colour(aeros, SKY, 'the screen going dark for Sleep')
    aeros.screenshot('desktop-sleep')
    time.sleep(1)
    aeros.key('spc')
    wait_for_colour(aeros, SKY, 'the desktop, woken from Sleep')
    wait_for_colour(aeros, TITLE_ON, 'the clock window, still open after Sleep')

    # Shut down: aerOS asks. No, and the desktop carries on
    menu_entry(aeros, ups=2)
    wait_for_colour(aeros, QUESTION, "aerOS's question before turning off")
    aeros.screenshot('desktop-shutdown-question')
    aeros.key('n')
    wait_without_colour(aeros, QUESTION, 'the question to go, after N')
    time.sleep(1)
    if aeros.proc.poll() is not None:
        aeros.fail('the computer turned off after N')
    wait_for_colour(aeros, SKY, 'the desktop, after saying no')

    # and yes: it turns off
    menu_entry(aeros, ups=2)
    wait_for_colour(aeros, QUESTION, "aerOS's question before turning off, again")
    aeros.key('y')
    deadline = time.time() + 60
    while aeros.proc.poll() is None:
        if time.time() > deadline:
            aeros.fail('the computer did not turn off after Y')
        time.sleep(0.3)


def make_bmp(path, colour, width=64, height=48):
    """A 24 bit BMP picture of one colour (3 bytes: red, green, blue), bottom row first."""
    row = bytes([colour[2], colour[1], colour[0]]) * width
    row += b'\0' * ((4 - len(row) % 4) % 4)
    pixels = row * height
    header = struct.pack('<2sIHHI', b'BM', 54 + len(pixels), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    with open(path, 'wb') as f:
        f.write(header + info + pixels)


def test_desktop_settings(t):
    """Settings: a theme and the taskbar's place are saved in desktop.cfg in the home folder and
    read back the next time (after a reboot), and a BMP from the home folder is chosen as the
    background."""
    disk = t.disk(files=t.package('desktop'))
    aeros = t.boot(graphics=True, disk=disk)
    aeros.first_login()
    before = start_desktop(aeros)

    # Settings is the 6th of the desktop's own; its first row is the colours: Right is Forest,
    # saved straight away (aerOS asks, the first time, to change a file in the home folder)
    menu_entry(aeros, downs=OWN_APPS.index('settings'))
    time.sleep(1)
    aeros.key('right')
    answer_question(aeros)
    wait_for_colour(aeros, FOREST_SKY, 'the Forest theme')
    # the third row is the taskbar: Right is the top
    aeros.key('down')
    aeros.key('down')
    time.sleep(0.3)
    aeros.key('right')
    time.sleep(1.5)
    shot = aeros.screenshot('desktop-settings')
    if not row_has(shot, 2, FOREST_TASKBAR):
        aeros.fail('the taskbar did not move to the top')
    # Esc closes Settings and the clock, then leaves
    for _ in range(3):
        aeros.key('esc')
        time.sleep(0.5)
    aeros.wait_prompt(before, 30, what='desktop')
    aeros.expect_not('Error')
    aeros.quit()
    saved = read_disk_file(disk, '/home/tester/desktop.cfg') or b''
    for line in (b'theme=Forest', b'taskbar=top', b'picture=\n'):
        if line not in saved:
            aeros.fail(f'desktop.cfg has no {line!r}: {saved!r}')

    # a picture in the home folder, and a second boot: the settings come back
    make_bmp(t.path('pic.bmp'), PICTURE)
    write_disk_file(disk, '/home/tester/pic.bmp', open(t.path('pic.bmp'), 'rb').read())
    aeros = t.boot(graphics=True, disk=disk)
    aeros.login()
    before = aeros.prompts()
    aeros.ask('desktop')
    # (no question: "always" was the answer last time)
    wait_for_colour(aeros, FOREST_SKY, 'the Forest theme, read back from desktop.cfg', 60)
    shot = aeros.screenshot('desktop-settings-back')
    if not row_has(shot, 2, FOREST_TASKBAR):
        aeros.fail('the taskbar is not at the top, after reading desktop.cfg back')

    # the background: the second row, Enter opens the picture chooser in the home folder:
    # its folders, then its pictures, of which pic.bmp is the only one, so it is last. Down
    # stops there, and Enter on it makes it the background
    menu_entry(aeros, downs=OWN_APPS.index('settings'))
    time.sleep(1)
    aeros.key('down')
    time.sleep(0.3)
    aeros.key('ret')
    time.sleep(1)
    aeros.screenshot('desktop-picture-chooser')
    for _ in range(6):
        aeros.key('down')
    time.sleep(0.5)
    aeros.key('ret')
    wait_for_colour(aeros, PICTURE, 'the picture as the background')
    aeros.screenshot('desktop-picture')
    for _ in range(3):
        aeros.key('esc')
        time.sleep(0.5)
    aeros.wait_prompt(before, 30, what='desktop')
    aeros.expect_not('Error')
    aeros.quit()
    saved = read_disk_file(disk, '/home/tester/desktop.cfg') or b''
    if b'picture=/home/tester/pic.bmp' not in saved:
        aeros.fail(f'desktop.cfg has no picture=/home/tester/pic.bmp: {saved!r}')


def test_desktop_monitor(t):
    """The system monitor: its indicators on the taskbar (the network's signal bars lit, the
    processor's graph), and its panel, opened with Home or a click on them and closed with Esc
    or a click outside it. Taking the network's link down (QEMU's set_link) puts the bars out
    within a couple of seconds, without the desktop starting the card that app mode put away."""
    disk = t.disk(files=t.package('desktop'))
    aeros = t.boot(graphics=True, disk=disk, network='user,model=e1000,id=n0')
    aeros.first_login()
    before = start_desktop(aeros)

    # the indicators, along the bottom of the screen (the taskbar is 34 pixels tall)
    wait_for_colour(aeros, LINK_UP, 'the network signal bars, lit')
    shot = aeros.screenshot('desktop-monitor-tray')
    if not row_has(shot, shot[1] - 17, SCREEN_BACK):
        aeros.fail("the processor's graph is not on the taskbar")
    if not row_has(shot, shot[1] - 17, LINK_UP):
        aeros.fail('the network signal bars are not on the taskbar')

    # Home opens the panel; it stays up to date while open, so pulling the network's cable
    # puts its bars out (on the taskbar and in the panel) within a look or two
    aeros.key('home')
    wait_for_colour(aeros, MONITOR_EDGE, 'the system monitor, after Home')
    aeros.screenshot('desktop-monitor')
    aeros.monitor('set_link n0 off')
    wait_without_colour(aeros, LINK_UP, 'the signal bars to go out, with the link down')
    aeros.screenshot('desktop-monitor-unplugged')
    aeros.key('esc')
    wait_without_colour(aeros, MONITOR_EDGE, 'the system monitor to close, after Esc')
    aeros.monitor('set_link n0 on')
    wait_for_colour(aeros, LINK_UP, 'the signal bars again, with the link back up')

    # the mouse: into the bottom left corner, then along the taskbar onto the indicators,
    # which sit just before the clock (130 to 225 pixels from the right end). QEMU sends a big
    # move in pieces, so it goes a step at a time
    for _ in range(8):
        aeros.mouse_move(-200, 200)
        time.sleep(0.1)
    width = shot[0]
    for _ in range(5):
        aeros.mouse_move((width - 175) // 5, 0)
        time.sleep(0.1)
    aeros.mouse_move(0, -17)
    time.sleep(0.5)
    aeros.mouse_buttons(1)
    time.sleep(0.2)
    aeros.mouse_buttons(0)
    wait_for_colour(aeros, MONITOR_EDGE, 'the system monitor, after clicking its indicators')
    # a click on the desk, left of the panel, closes it
    aeros.mouse_move(-(width // 2), -150)
    time.sleep(0.5)
    aeros.mouse_buttons(1)
    time.sleep(0.2)
    aeros.mouse_buttons(0)
    wait_without_colour(aeros, MONITOR_EDGE, 'the system monitor to close, after a click outside')

    # Esc closes the clock, then leaves the desktop
    aeros.key('esc')
    time.sleep(0.5)
    aeros.key('esc')
    aeros.wait_prompt(before, 30, what='desktop')
    aeros.expect('Left the desktop.')
    aeros.expect_not('Error')


def answer_question(aeros, answer='a', timeout=30):
    """Wait for aerOS's question box, answer it (A: always), and wait for it to go."""
    wait_for_colour(aeros, QUESTION, "aerOS's question, before the file was used", timeout)
    aeros.key(answer)
    deadline = time.time() + timeout
    while has(aeros.screenshot('desktop'), QUESTION):
        if time.time() > deadline:
            aeros.fail('the question stayed after it was answered')
        time.sleep(0.3)
