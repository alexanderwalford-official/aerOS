"""Updating AerOS on an installed disk (src/sys/update.h): publishing a new AerOS with
aerpkg.py publish-os, osupdate() checking it and putting it on the disk, the new one starting,
and GRUB going back to the one from before when the new one doesn't start.

Everything is signed with a test key made here, never the real one. The kernels are copies of
the one built, changed the way a new build would be: the test key in place of the real one
(AEROS_PACKAGE_KEY, which osupdate() checks against), and the build number in the build stamp
set to a time of the test's choosing."""

import os
import re
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_packages import Server, must  # noqa: E402  (the package server, with a test key)

from aeros import GUEST_PROMPT  # noqa: E402

# the build numbers of the three kernels: installed, a good update, and an update that never starts
FIRST, SECOND, BROKEN = '20200101000000', '20300101000000', '20400101000000'
STAMP = re.compile(rb'\x01AEROS-BUILD:([0-9]{14}) ')


def kernel_copy(t, name, public_key, build, never_starts=False):
    """A copy of the built main.elf with the test's key in it and this build number. With
    never_starts its first instruction is "jump to itself": GRUB starts it, and it goes no further."""
    with open(t.kernel, 'rb') as f:
        data = bytearray(f.read())
    real_key = re.search(rb'#define AEROS_PACKAGE_KEY "([0-9a-f]{64})"',
                         open(t.repo('src', 'sys', 'package_key.h'), 'rb').read()).group(1)
    must(real_key in data, 'the package key is not in main.elf as text')
    data = bytearray(bytes(data).replace(real_key, public_key.encode()))
    stamps = list(STAMP.finditer(data))
    must(len(stamps) == 1, f'main.elf should have one build stamp, not {len(stamps)}')
    data[stamps[0].start(1):stamps[0].end(1)] = build.encode()
    if never_starts:
        # the ELF header: where it starts (e_entry), and the program headers that say which part
        # of the file is loaded where
        entry, = struct.unpack_from('<I', data, 24)
        phoff, = struct.unpack_from('<I', data, 28)
        phentsize, phnum = struct.unpack_from('<HH', data, 42)
        for i in range(phnum):
            kind, offset, vaddr, _, filesz = struct.unpack_from('<IIIII', data, phoff + i * phentsize)
            if kind == 1 and vaddr <= entry < vaddr + filesz:
                data[offset + entry - vaddr:offset + entry - vaddr + 2] = b'\xeb\xfe'   # jmp $
                break
        else:
            raise AssertionError('could not find main.elf\'s first instruction')
    path = t.path(name)
    with open(path, 'wb') as f:
        f.write(data)
    return path


def on_partition(disk, path):
    """A file from the FAT volume in the disk's partition (it starts 1 MB in), or None."""
    done = subprocess.run(['mtype', '-i', disk + '@@1M', '::' + path], capture_output=True)
    return done.stdout if done.returncode == 0 else None


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def update_setting(disk):
    """aeros_update in /grubenv on the disk ('' if it isn't set)."""
    match = re.search(rb'^aeros_update=(\w*)$', on_partition(disk, '/grubenv') or b'', re.M)
    return match.group(1).decode() if match else ''


def put_update_app(t, disk, port):
    """The update app (src/disk/bin/update.aer) on the installed disk as /bin/updt.aer, a copy
    that fetches from this computer's test server instead of the real one."""
    with open(t.repo('src', 'disk', 'bin', 'update.aer'), 'rb') as f:
        app = f.read()
    real = re.search(rb'let SERVER = "[^"]*"', app)
    must(real is not None, 'update.aer should set SERVER in one line: let SERVER = "..."')
    app = app.replace(real.group(0), f'let SERVER = "http://10.0.2.2:{port}"'.encode())
    path = t.path('updt.aer')
    with open(path, 'wb') as f:
        f.write(app)
    done = subprocess.run(['mcopy', '-o', '-i', disk + '@@1M', path, '::/bin/updt.aer'], capture_output=True)
    must(done.returncode == 0, f'could not put updt.aer on the disk: {done.stderr!r}')


def said_by(aeros, line, timeout=60):
    """Run a line at the prompt, and give back what was printed after it (its echo left out)."""
    start = len(aeros.serial())
    aeros.run(line, timeout=timeout)
    printed = aeros.serial()[start:]
    return printed.split('\n', 1)[1] if '\n' in printed else ''


def quiet(printed):
    """Nothing printed but the next prompt: no [ ! ], [ X ] or any other message. (App mode's line
    is only on the serial port, never the screen: src/sys/appmode.c)"""
    return all(line.strip() == '' or re.match(r'\S+@\d+//\S*: $', line) or line.startswith('app mode: ')
               for line in printed.split('\n'))


def start_from_disk(t, disk, network=False):
    """Start AerOS from the installed disk alone, as a PC would, and log in."""
    aeros = t.boot(disk=disk, from_disk=True, network='user,model=e1000' if network else None)
    aeros.login()
    return aeros


def test_publish_os(t):
    # the server side: publish-os puts AerOS on the server with an #os line, an older one is
    # refused, and publishing packages afterwards keeps it
    server = Server(t)
    try:
        first = kernel_copy(t, 'first.elf', server.public, FIRST)
        second = kernel_copy(t, 'second.elf', server.public, SECOND)
        server.aerpkg('publish-os', second)
        status, index = server.get('/index.txt')
        must(status == 200, 'there should be an index after publish-os')
        line = [l for l in index.decode().split('\n') if l.startswith('#os ')]
        must(len(line) == 1 and line[0].startswith(f'#os {SECOND} ') and line[0].endswith(' 0.01 ALPHA'),
             f'the index should have one #os line for build {SECOND}: {line}')
        must(server.get('/os/main.elf') == (200, read(second)), '/os/main.elf should be the kernel published')

        refused = server.aerpkg('publish-os', first, check=False)
        must(refused.returncode != 0 and 'only a newer one' in refused.stdout + refused.stderr,
             'publish-os should refuse an older build than the one on the server')

        program = server.program('hi', 'print("hi")\n')
        server.aerpkg('publish', program, '1.0', 'Says hi')
        index = server.get('/index.txt')[1].decode()
        must(f'#os {SECOND} ' in index and '\nhi 1.0 ' in index,
             'publishing a package should keep the #os line')
        must(server.get('/os/main.elf') == (200, read(second)), 'AerOS should still be served')
    finally:
        server.stop()


def test_check_is_quiet_when_not_installed(t):
    # On an AEROS disk that wasn't made by INSTALL (AerOS started some other way), there is
    # nothing to update: osupdatable() says why, osupdatable(false) doesn't, and update check
    # (the real one, as it is built in) says nothing at all
    aeros = t.boot(disk=t.disk(), network='user,model=e1000')
    aeros.first_login()
    aeros.cls()
    aeros.run('print(osupdatable(false))')
    aeros.expect_line('false')
    aeros.expect_not('[ ! ]')
    aeros.cls()
    aeros.run('update check', timeout=60)
    aeros.expect_not('[ ! ]', '[ X ]', 'available')
    aeros.cls()
    aeros.run('print(osupdatable())')
    aeros.expect("[ ! ] This AEROS disk has no /boot/main.elf")
    aeros.expect_line('false')
    aeros.quit()


def test_update_and_go_back(t):
    server = Server(t)
    try:
        first = kernel_copy(t, 'first.elf', server.public, FIRST)
        second = kernel_copy(t, 'second.elf', server.public, SECOND)
        broken = kernel_copy(t, 'broken.elf', server.public, BROKEN, never_starts=True)

        # AerOS (the first build) installed on a new disk, from demo mode
        disk = t.path('installed.img')
        with open(disk, 'wb') as f:
            f.truncate(64 * 1024 * 1024)
        aeros = t.boot(disk=disk, kernel=first, words='fast demo', module=first)
        aeros.wait_for(GUEST_PROMPT, 60)
        before = aeros.prompts()
        aeros.ask('install(1)')
        aeros.wait_for('EVERYTHING on disk 1', 30)
        aeros.key('y')
        aeros.wait_prompt(before, 120, what='install(1)')
        aeros.quit()
        aeros = t.boot(disk=disk, from_disk=True)
        aeros.first_login()
        aeros.quit()
        put_update_app(t, disk, server.port)

        # ---- a good update -----------------------------------------------------------------
        server.aerpkg('publish-os', second)
        index = server.get('/index.txt')[1].decode()
        signature = re.search(r'^#os [0-9]{14} ([0-9a-f]{128}) ', index, re.M).group(1)
        url = f'http://10.0.2.2:{server.port}/os/main.elf'

        aeros = start_from_disk(t, disk, network=True)
        aeros.run('print("build " + osbuild() + ", version " + osversion())')
        aeros.expect(f'build {FIRST}, version 0.01 ALPHA')
        aeros.run('print(osupdatable())')
        aeros.expect_line('true')

        # no program may change what starts AerOS, and isn't asked (a question would hang this)
        aeros.run('wrf("/bin/sneaky.aer", "print(wrf(" + chr(34) + "/boot/main.elf" + chr(34) + ", "'
                  ' + chr(34) + "x" + chr(34) + "))")')
        aeros.run('sneaky', timeout=30)

        # update check, before the firewall lets AerOS reach the server: not a word (it is for
        # startup.txt, at every login, network or not)
        printed = said_by(aeros, 'updt check')
        must(quiet(printed), f'update check should say nothing when it can\'t reach the server: {printed!r}')

        aeros.run(f'fw web 10.0.2.2 {server.port}')
        # ...and once it can, one line: there is a newer one
        printed = said_by(aeros, 'updt check')
        must('[ ! ] aerOS 0.01 ALPHA is available (built 2030-01-01 00:00): type update' in printed,
             f'update check should say a newer aerOS is available: {printed!r}')
        aeros.run(f'let k = httpget("{url}")', timeout=120)
        aeros.run(f'let s = "{signature[:64]}"')
        aeros.run(f'let s2 = "{signature[64:]}"')
        # a wrong signature: refused before anything is asked
        aeros.run('print(osupdate(k, s2 + s))', timeout=120)
        aeros.expect("isn't signed by AerOS's makers")

        # the update app: it shows both, and N changes nothing
        before = aeros.prompts()
        aeros.ask('updt')
        aeros.wait_for(f'Update aerOS to build {SECOND}? (y/n)', 60)
        aeros.expect(f'Installed:  aerOS 0.01 ALPHA  (build {FIRST}, 2020-01-01 00:00)',
                     f'Available:  aerOS 0.01 ALPHA  (build {SECOND}, 2030-01-01 00:00)',
                     "[ ! ] Don't turn the computer off while the update is being written.")
        aeros.type('n\n')
        aeros.wait_prompt(before, 60, what='updt (no)')
        aeros.expect('[ X ] Update cancelled: nothing was changed.')
        # Y: it downloads it, AerOS asks too (then for the password), and it is put on the disk
        before = aeros.prompts()
        aeros.ask('updt')
        aeros.wait_for(f'Update aerOS to build {SECOND}? (y/n)', 60)
        aeros.type('y\n')
        aeros.wait_for('wants to update AerOS to build ' + SECOND, 120)
        aeros.key('y')
        aeros.answer('secret1')
        aeros.wait_prompt(before, 120, what='updt (yes)')
        aeros.expect('[ OK ] Downloaded ', f'[ OK ] aerOS 0.01 ALPHA (build {SECOND}) is ready.',
                     '[ ! ] Type rbt to restart into it.')
        aeros.quit()

        must(on_partition(disk, '/boot/main.elf') == read(second), '/boot/main.elf should be the update')
        must(on_partition(disk, '/boot/old.elf') == read(first),
             '/boot/old.elf should be the AerOS from before (and not changed by sneaky.aer)')
        must(update_setting(disk) == 'new', f'/grubenv should say new, not {update_setting(disk)!r}')

        # it starts, says so, and GRUB stops trying it
        aeros = start_from_disk(t, disk, network=True)
        aeros.expect_printed(f'Updated: this is AerOS 0.01 ALPHA, build {SECOND}')
        aeros.run('print("build " + osbuild())')
        aeros.expect(f'build {SECOND}')

        # the app says so, now that it has the newest
        aeros.run(f'fw web 10.0.2.2 {server.port}')
        aeros.run('updt', timeout=60)
        aeros.expect(f'[ OK ] aerOS is up to date: 0.01 ALPHA, build {SECOND}')
        printed = said_by(aeros, 'updt check')
        must(quiet(printed), f'update check should say nothing when it is up to date: {printed!r}')

        # the same one again is refused: it isn't newer than the one on the disk
        aeros.run(f'fw web 10.0.2.2 {server.port}')
        aeros.run(f'let k = httpget("{url}")', timeout=120)
        aeros.run(f'let s = "{signature[:64]}"')
        aeros.run(f'print(osupdate(k, s + "{signature[64:]}"))', timeout=120)
        aeros.expect(f"(build {SECOND}) isn't newer than the one on this disk")
        aeros.quit()
        must(update_setting(disk) == '', 'the update that started should have taken aeros_update out')

        # ---- an update that never starts ---------------------------------------------------
        server.aerpkg('publish-os', broken)
        index = server.get('/index.txt')[1].decode()
        signature = re.search(r'^#os [0-9]{14} ([0-9a-f]{128}) ', index, re.M).group(1)
        aeros = start_from_disk(t, disk, network=True)
        aeros.run(f'fw web 10.0.2.2 {server.port}')
        aeros.run(f'let k = httpget("{url}")', timeout=120)
        aeros.run(f'let s = "{signature[:64]}"')
        aeros.ask(f'print(osupdate(k, s + "{signature[64:]}"))')
        aeros.wait_for('AerOS to build ' + BROKEN, 120)
        before = aeros.prompts()
        aeros.key('y')
        aeros.answer('secret1')
        aeros.wait_prompt(before, 120, what='osupdate')
        aeros.quit()
        must(on_partition(disk, '/boot/main.elf') == read(broken), '/boot/main.elf should be the broken one')

        # GRUB starts it once, writing down that it tried: it gets nowhere
        aeros = t.boot(disk=disk, from_disk=True)
        time.sleep(25)
        if '[ OK ]' in aeros.serial():
            aeros.fail('the broken AerOS should not have got as far as its boot log')
        aeros.quit()
        must(update_setting(disk) == 'tried', f'GRUB should have saved tried, not {update_setting(disk)!r}')

        # started again, GRUB goes back to the one from before, which puts itself back
        aeros = start_from_disk(t, disk)
        aeros.expect_printed(f"The update to build {BROKEN} didn't start, so AerOS went back to build {SECOND}")
        aeros.run('print("build " + osbuild())')
        aeros.expect(f'build {SECOND}')
        aeros.quit()
        must(on_partition(disk, '/boot/main.elf') == read(second), '/boot/main.elf should be the good one again')
        must(on_partition(disk, '/boot/old.elf') is None, '/boot/old.elf should be gone')
        must(update_setting(disk) == '', '/grubenv should not say anything about an update any more')

        # and from then on it starts as usual
        aeros = start_from_disk(t, disk)
        aeros.run('print("build " + osbuild())')
        aeros.expect(f'build {SECOND}')
        aeros.quit()
    finally:
        server.stop()
