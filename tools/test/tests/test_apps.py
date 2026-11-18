"""The built-in apps: the manual reader, the log viewer (aerlog), and how built-in apps are
installed onto a disk, kept up to date, and taken away once AerOS doesn't come with them (see
the top of src/sys/apps.c). And the animation demo, which is a package now."""

import hashlib
import re
import time

from aeros import GUEST_PROMPT, Failure, delete_disk_file, read_disk_file, write_disk_file


def quit_app(aeros, key='q'):
    """Press the key that leaves an app, and wait for the terminal to be back."""
    before = aeros.prompts()
    aeros.key(key)
    aeros.wait_prompt(before, 20)


def test_doc_reader(t):
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.ask('doc')
    aeros.wait_for('The AerOS manual', 60)
    quit_app(aeros)

    aeros.ask('doc security')
    aeros.wait_for('Accounts and encryption', 60)
    quit_app(aeros)

    # the same reader shows any text file
    aeros.ask('read /bin/hello.aer')
    aeros.wait_for('hello.aer - a first', 60)
    quit_app(aeros)


def test_animations(t):
    # anim is a package: installed on the disk the way pkg install puts it
    aeros = t.boot(disk=t.disk(files=t.package('anim')))
    aeros.first_login()
    aeros.ask('anim')
    aeros.wait_for('Animations', 30)
    time.sleep(0.5)
    first = aeros.screen()
    time.sleep(1.3)
    if aeros.screen() == first:
        aeros.fail('nothing moved in 1.3 seconds')
    quit_app(aeros, 'spc')


def boot_and_stop(t, disk):
    """Boot with the disk until AerOS asks for an account (the apps are installed before that),
    then stop. Gives what AerOS printed."""
    aeros = t.boot(disk=disk)
    aeros.wait_for('Create the first account', 180)
    printed = aeros.serial()
    aeros.quit()
    return printed


def test_built_in_apps_are_installed(t):
    disk = t.disk()
    built_in_hello = open(t.repo('src', 'disk', 'bin', 'hello.aer'), 'rb').read()
    built_in_sysinfo = open(t.repo('src', 'disk', 'bin', 'sysinfo.aer'), 'rb').read()

    # a new disk gets every built-in file, and a record of what it was given
    printed = boot_and_stop(t, disk)
    added = re.search(r'(\d+) built-in file\(s\) added or updated', printed)
    if not added or int(added.group(1)) < 10:
        raise Failure('the built-in files were not added:\n' + printed[-800:])
    if read_disk_file(disk, '/bin/hello.aer') != built_in_hello:
        raise Failure('/bin/hello.aer is not the built-in copy')
    record = read_disk_file(disk, '/sys/apps.lst').decode()
    if '/bin/sysinfo.aer ' not in record:
        raise Failure('/sys/apps.lst has no line for sysinfo.aer:\n' + record)

    # booting again has nothing to do
    if 'added or updated' in boot_and_stop(t, disk):
        raise Failure('the second boot added files again')

    # an app changed by hand is kept, and so is the change
    write_disk_file(disk, '/bin/hello.aer', 'print("my own hello")\n')
    boot_and_stop(t, disk)
    boot_and_stop(t, disk)
    if read_disk_file(disk, '/bin/hello.aer') != b'print("my own hello")\n':
        raise Failure('a changed hello.aer was replaced')

    # an older copy that nobody changed (its hash is the one in the record) is brought up to date
    older = b'# an older sysinfo\n'
    record = read_disk_file(disk, '/sys/apps.lst').decode()
    record = re.sub(r'^/bin/sysinfo\.aer \S+', '/bin/sysinfo.aer ' + hashlib.sha256(older).hexdigest(),
                    record, flags=re.M)
    write_disk_file(disk, '/sys/apps.lst', record)
    write_disk_file(disk, '/bin/sysinfo.aer', older)
    boot_and_stop(t, disk)
    if read_disk_file(disk, '/bin/sysinfo.aer') != built_in_sysinfo:
        raise Failure('an untouched older sysinfo.aer was not updated')

    # a deleted app comes back
    delete_disk_file(disk, '/bin/sysinfo.aer')
    boot_and_stop(t, disk)
    if read_disk_file(disk, '/bin/sysinfo.aer') != built_in_sysinfo:
        raise Failure('a deleted sysinfo.aer did not come back')
    t.check_disk(disk)


def test_old_built_in_files_are_taken_away(t):
    # A disk from an older AerOS, which came with Snake and a picture: their copies, and the
    # record saying AerOS put them there. Now they are packages, so the copies nobody changed go
    # (an old /bin/games/snake.aer would otherwise run in place of an installed snake package)
    old_snake = b'print("the snake an older AerOS came with")\n'
    old_picture = b'BM an older picture'
    mine = b'print("my own version of the old maze")\n'
    for name, data in (('snake.aer', old_snake), ('aeros.bmp', old_picture), ('maze3d.aer', mine)):
        with open(t.path(name), 'wb') as f:
            f.write(data)
    disk = t.disk(files={'/bin/games/snake.aer': t.path('snake.aer'),
                         '/bin/games/maze3d.aer': t.path('maze3d.aer'),
                         '/bin/aeros.bmp': t.path('aeros.bmp')})
    printed = boot_and_stop(t, disk)
    record = read_disk_file(disk, '/sys/apps.lst').decode()
    # what the record would have said: maze3d.aer as an older AerOS put it, before it was changed
    record += ''.join(f'{path} {hashlib.sha256(data).hexdigest()}\n' for path, data in (
        ('/bin/games/snake.aer', old_snake), ('/bin/aeros.bmp', old_picture),
        ('/bin/games/maze3d.aer', b'the maze an older AerOS came with\n')))
    write_disk_file(disk, '/sys/apps.lst', record)

    printed = boot_and_stop(t, disk)
    if '2 old built-in file(s) removed' not in printed:
        raise Failure('the old built-in files were not taken away:\n' + printed[-800:])
    if read_disk_file(disk, '/bin/games/snake.aer') is not None or read_disk_file(disk, '/bin/aeros.bmp') is not None:
        raise Failure('an unchanged old built-in file is still there')
    # the one you changed is yours: it stays, and the record forgets all three
    if read_disk_file(disk, '/bin/games/maze3d.aer') != mine:
        raise Failure('a changed old built-in file was taken away')
    record = read_disk_file(disk, '/sys/apps.lst').decode()
    if '/bin/games/' in record or '/bin/aeros.bmp' in record:
        raise Failure('the record still lists files AerOS no longer comes with:\n' + record)
    # and nothing to do the next time
    if 'removed' in boot_and_stop(t, disk):
        raise Failure('the next boot took files away again')
    t.check_disk(disk)


def wait_printed(aeros, text, times, timeout=30):
    """Wait until `text` has been printed `times` times since boot. The serial log keeps
    everything, so this works for apps that clear the screen between steps."""
    start = time.time()
    while aeros.serial().count(text) < times:
        if time.time() - start > timeout:
            aeros.fail(f'waited {timeout}s for {text!r} to have been printed {times} time(s)')
        time.sleep(0.05)


def test_aerlog(t):
    # aerlog.aer, the log viewer: every option of its menu, in turn
    disk = t.disk()
    aeros = t.boot(disk=disk)
    aeros.first_login()
    before = aeros.prompts()
    aeros.ask('aerlog')
    menu = 'Enter your selection: '
    pause = 'Press Enter to continue...'

    # 1: the boots
    wait_printed(aeros, menu, 1)
    aeros.type('1\n')
    wait_printed(aeros, pause, 1)
    aeros.type('\n')

    # 2: the entries
    wait_printed(aeros, menu, 2)
    aeros.type('2\n')
    wait_printed(aeros, pause, 2)
    aeros.type('\n')

    # 4: turn logging off (AerOS asks, then wants the password)
    wait_printed(aeros, menu, 3)
    aeros.type('4\n')
    wait_printed(aeros, '[ ? ]', 1)
    aeros.key('y')
    aeros.answer('secret1')
    wait_printed(aeros, pause, 3)
    aeros.type('\n')

    # 3: clear the log, answering no
    wait_printed(aeros, menu, 4)
    aeros.type('3\n')
    wait_printed(aeros, '[ ? ]', 2)
    aeros.key('n')
    wait_printed(aeros, pause, 4)
    aeros.type('\n')

    # a choice that isn't on the menu, then 5: exit
    wait_printed(aeros, menu, 5)
    aeros.type('9\n')
    wait_printed(aeros, pause, 5)
    aeros.type('\n')
    wait_printed(aeros, menu, 6)
    aeros.type('5\n')
    aeros.wait_prompt(before, 30, what='aerlog')

    aeros.expect_printed(
        'System logging is currently ENABLED.',
        'Boot 1  started 20', 'running now',                    # option 1
        '[warning]  sound  No sound card found',                # option 2
        "aerlog.aer wants to turn AerOS's logging off",         # option 4
        'System logging is now DISABLED.',
        'System logging is currently DISABLED.',
        "aerlog.aer wants to clear AerOS's log",                # option 3
        'The logs were left as they are.',
        'Invalid selection. Please try again.',
        'Exiting the Aerlog utility.')
    if 'Error in aerlog.aer' in aeros.serial():
        aeros.fail('aerlog.aer stopped with an error')

    # and AerOS agrees: logging is off
    aeros.cls()
    aeros.run('islogging()')
    aeros.expect_line('false')
    aeros.quit()
    t.check_disk(disk)


def test_installed_apps_run_by_name(t):
    # /apps/ is where installed packages go: made at boot, and searched after /bin/ and
    # /bin/games/, so an app there runs by its name, but can't take the place of a built-in one
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.run('mkf /apps/hiya.aer print("hi from an installed app")')
    aeros.run('mkf /apps/sysinfo.aer print("not the real sysinfo")')
    aeros.cls()
    aeros.run('hiya')
    aeros.expect_line('hi from an installed app')
    aeros.cls()
    aeros.run('scan sysinfo')
    aeros.expect('/bin/sysinfo.aer only works with its own values')
