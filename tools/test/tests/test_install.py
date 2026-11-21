"""Installing AerOS on a disk (src/sys/install.h): disks() and install() in demo mode, and then
starting AerOS from that disk alone, with no CD and no kernel given to QEMU.

The test boots the kernel the way the other text mode tests do, with "demo" on its command line
(as picking 3 in the boot options does), and hands it main.elf as a module with QEMU's -initrd,
the way GRUB on the CD does (iso/boot/grub/grub.cfg)."""

import os
import subprocess
import time

from aeros import GUEST_PROMPT


def blank_disk(t, name, size_mb):
    """A disk image of zeros: a new disk, with nothing on it at all."""
    path = t.path(name)
    with open(path, 'wb') as f:
        f.truncate(size_mb * 1024 * 1024)
    return path


def on_partition(disk, path):
    """A file from the FAT volume in the disk's partition (it starts 1 MB in), or None."""
    done = subprocess.run(['mtype', '-i', disk + '@@1M', '::' + path], capture_output=True)
    return done.stdout if done.returncode == 0 else None


def test_install_and_start_from_the_disk(t):
    disk = blank_disk(t, 'new.img', 64)
    aeros = t.boot(disk=disk, words='fast demo', module=t.kernel)
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.expect('DEMO mode')

    # the disk, and that nothing is on it
    aeros.cls()
    aeros.run('disks()')
    aeros.expect('QEMU HARDDISK', '64', 'nothing')

    # AerOS asks first: no erases nothing
    aeros.cls()
    aeros.ask('install(1)')
    aeros.wait_for('EVERYTHING on disk 1', 30)
    aeros.key('n')
    aeros.wait_for('Nothing was changed.', 30)
    aeros.cls()
    aeros.run('disks()')
    aeros.expect('nothing')

    # yes installs it
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('install(1)')
    aeros.wait_for('EVERYTHING on disk 1', 30)
    aeros.key('y')
    aeros.wait_prompt(before, 120, what='install(1)')
    aeros.expect('a FAT32 volume labelled AEROS...  done', 'checking main.elf on the disk...  done')
    aeros.expect_line('true')
    aeros.cls()
    aeros.run('disks()')
    aeros.expect('AerOS')
    aeros.quit()

    # what is on it: AerOS itself and GRUB's menu, as built
    with open(t.kernel, 'rb') as f:
        if on_partition(disk, '/boot/main.elf') != f.read():
            aeros.fail('/boot/main.elf on the disk is not the kernel that was installed')
    with open(t.repo('src', 'install', 'grub.cfg'), 'rb') as f:
        if on_partition(disk, '/boot/grub/grub.cfg') != f.read():
            aeros.fail('/boot/grub/grub.cfg on the disk is not src/install/grub.cfg')

    # Started from the disk alone: GRUB on it starts AerOS, which finds a new AEROS disk, makes
    # its folders and apps, and asks for the first account
    aeros = t.boot(disk=disk, from_disk=True)
    aeros.first_login()
    aeros.expect('DISK mode')
    aeros.cls()
    aeros.run('print(la("/bin/"))')
    aeros.expect('hello.aer', 'pkg.aer')
    aeros.quit()
    if on_partition(disk, '/bin/hello.aer') is None:
        aeros.fail('AerOS started from the disk did not put its apps on it')


def test_install_only_from_demo_mode(t):
    # On a normal boot a disk may be in use (the AEROS disk), so install() refuses without
    # asking anything, and without touching the disk
    disk = blank_disk(t, 'other.img', 64)
    aeros = t.boot(disk=disk, module=t.kernel)
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run('install(1)')
    aeros.expect('can only be installed from demo mode')
    aeros.expect_line('false')
    aeros.quit()
    with open(disk, 'rb') as f:
        if f.read(1024 * 1024).strip(b'\0'):
            aeros.fail('install() outside demo mode wrote to the disk')


SPLASH_ORANGE = bytes([0xFF, 0x7F, 0x00])   # the splash screen's background (sys/splash.c)


def wait_for_corner(aeros, wanted, what, timeout=90):
    """Take screenshots until the colour of the screen's top left pixel is one wanted(colour)
    says yes to."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, pixels = aeros.screenshot('corner')
        if wanted(pixels[0:3]):
            return
        time.sleep(0.2)
    aeros.fail(f'waited {timeout}s for {what}')


def test_install_from_the_cd(t):
    # The real way: AerOS started from its CD (main.iso), demo mode picked in the boot options
    # (X on the splash screen, then 3), and GRUB on the CD handing it main.elf as a module
    disk = blank_disk(t, 'cd.img', 64)
    aeros = t.boot(graphics=True, disk=disk)
    # The splash screen and the boot options are drawn straight on the screen, so they aren't
    # in the serial log to wait for: the screenshots are watched instead. The splash screen is
    # orange all over, and the boot options screen isn't
    wait_for_corner(aeros, lambda colour: colour == SPLASH_ORANGE, 'the splash screen')
    aeros.key('x')
    wait_for_corner(aeros, lambda colour: colour != SPLASH_ORANGE, 'the boot options')
    aeros.key('3')
    aeros.wait_for(GUEST_PROMPT, 90)
    aeros.expect('DEMO mode')
    before = aeros.prompts()
    aeros.ask('install(1)')
    aeros.wait_for('EVERYTHING on disk 1', 30)
    aeros.key('y')
    aeros.wait_prompt(before, 120, what='install(1)')
    aeros.expect('checking main.elf on the disk...  done')
    aeros.quit()
    with open(t.repo('src', 'iso', 'boot', 'main.elf'), 'rb') as f:
        if on_partition(disk, '/boot/main.elf') != f.read():
            aeros.fail('/boot/main.elf on the disk is not the main.elf on the CD')


def heap_total(aeros):
    """The kernel heap's size in bytes, from MEM ("Kernel heap: used / total bytes used")."""
    aeros.cls()
    aeros.run('mem')
    line = [l for l in aeros.screen().split('\n') if 'Kernel heap:' in l][0]
    return int(line.split('/')[1].split()[0])


def test_the_copy_is_only_kept_for_demo_mode(t):
    # GRUB loads a copy of AerOS for the installer on every boot from the CD. Outside demo mode
    # nothing can use it, so its memory joins the heap; in demo mode it is kept
    plain = t.boot()
    plain.wait_for(GUEST_PROMPT, 60)
    without = heap_total(plain)
    plain.quit()
    normal = t.boot(module=t.kernel)
    normal.wait_for(GUEST_PROMPT, 60)
    given_back = heap_total(normal)
    normal.quit()
    demo = t.boot(words='fast demo', module=t.kernel)
    demo.wait_for(GUEST_PROMPT, 60)
    kept = heap_total(demo)
    size = os.path.getsize(t.kernel)
    if abs(given_back - without) > 64 * 1024:
        demo.fail(f'a normal boot did not give the copy back: heap {given_back} bytes, {without} without one')
    if without - kept < size - 64 * 1024:
        demo.fail(f'demo mode did not keep the copy: heap {kept} bytes, {without} without one')
