"""The screen's resolution, and the boot log's line about the network card, on the graphics
cards and network cards VirtualBox gives a PC (see docs/SCREEN.md and docs/NETWORK.md).

RES saves a resolution as /grubenv on the AEROS disk, and GRUB asks the firmware (VBE) for it
at the next start. The firmware only knows a fixed list of sizes, so a size it doesn't know
must not leave AerOS in text mode: GRUB falls back to 1024x768 (grub.cfg), and AerOS then
changes to the saved size itself through the Bochs Graphics Adapter (restore_resolution in
src/sys/boot.c)."""


def grubenv(t, mode):
    """A /grubenv file as RES writes it, asking for this mode ("1280x720x32")."""
    path = t.path('grubenv')
    block = f'# GRUB Environment Block\naeros_mode={mode}\n'
    open(path, 'w', newline='\n').write(block + '#' * (1024 - len(block)))
    return {'/grubenv': path}


def test_saved_resolution_firmware_has(t):
    # 1280x720 is in the firmware's list: GRUB sets it, and AerOS leaves it alone
    aeros = t.boot(graphics=True, disk=t.disk(files=grubenv(t, '1280x720x32')))
    aeros.wait_for('[VFS:UP]', 90)
    aeros.expect('Screen: 1280x720 framebuffer')
    aeros.expect_not('the size saved with RES')
    width, height, _ = aeros.screenshot()
    if (width, height) != (1280, 720):
        aeros.fail(f'the screen is {width}x{height}, not 1280x720')


def test_saved_resolution_firmware_lacks(t):
    # 1001x701 is in no firmware's list (VirtualBox's has no 1920x1080 either; QEMU's has more
    # sizes, even 1000x700, but not an odd one). GRUB used to fall back to text mode here; now
    # it starts at 1024x768, and AerOS changes to the saved size, which QEMU's card rounds to
    # a width that is a multiple of 8
    aeros = t.boot(graphics=True, disk=t.disk(files=grubenv(t, '1001x701x32')))
    aeros.wait_for('[VFS:UP]', 90)
    aeros.expect('Screen: 1024x768 framebuffer',
                 'Screen: 1000x701, the size saved with RES (the firmware started at 1024x768)')
    width, height, _ = aeros.screenshot()
    if (width, height) != (1000, 701):
        aeros.fail(f'the screen is {width}x{height}, not 1000x701')


def test_network_card_82545em(t):
    # VirtualBox's "Intel PRO/1000 MT Server" (8086:100F) works like the 82540EM. The ISO is
    # booted so the boot log can be read from the serial port, where it stays
    aeros = t.boot(graphics=True, network='user,model=e1000-82545em')
    aeros.wait_for('Network:', 60)
    aeros.expect('Network: Intel e1000 100f')


def test_network_card_pcnet(t):
    # VirtualBox's usual card, which AerOS has no driver for: the boot log says what to pick
    aeros = t.boot(graphics=True, network='user,model=pcnet')
    aeros.wait_for('PCnet', 60)
    aeros.expect('AMD PCnet network card: no driver. In VirtualBox, pick Intel PRO/1000 MT Desktop')
