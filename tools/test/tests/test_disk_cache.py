"""The disk cache (src/sys/disk_cache.c): writes are held in memory and put on the disk together.

These check the promise in disk_cache.h: whenever AerOS is back at the prompt, waiting for a key,
or a program has been sleeping for a second, nothing is left waiting. Each one stops QEMU
abruptly (like pulling the plug: nothing gets a chance to write anything more), then reads the
disk image from outside and checks the file is there."""

from aeros import read_disk_file, wait_for_disk_file


def program(t, name, source):
    """Write an AerLang program to this test's folder, for t.disk(files=...)."""
    path = t.path(name)
    with open(path, 'w') as f:
        f.write(source)
    return path


def allow_writing(aeros):
    """Answer A (always) to AerOS asking whether the program may change files."""
    aeros.wait_for('wants to change', 60)
    aeros.key('a')


def test_writes_reach_the_disk(t):
    disk = t.disk(files={
        # writes, then sleeps far longer than the second a change may wait
        '/bin/sleeper.aer': program(t, 'sleeper.aer',
                                    'wrf("/home/tester/slept.txt", "written before sleeping")\n'
                                    'print("written")\n'
                                    'sleep(60000)\n'),
        # writes, then waits for a key
        '/bin/asker.aer': program(t, 'asker.aer',
                                  'wrf("/home/tester/asked.txt", "written before asking")\n'
                                  'input("Anything? ")\n')})

    # at the prompt: the change is on the disk by the time the prompt comes back
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.run('mkf prompt.txt written at the prompt')
    aeros.quit()
    if read_disk_file(disk, '/home/tester/prompt.txt') != b'written at the prompt':
        aeros.fail('a file made at the prompt was not on the disk when the prompt came back')

    # a program sleeping: written within about a second, while it sleeps
    aeros = t.boot(disk=disk)
    aeros.login()
    aeros.ask('sleeper')
    allow_writing(aeros)
    aeros.wait_for('written', 60)
    # (it sleeps for a minute, so whatever puts the change on the disk, it isn't the program
    # ending: it is the disk writer thread, while the program sleeps)
    if not wait_for_disk_file(disk, '/home/tester/slept.txt', b'written before sleeping'):
        aeros.fail('a sleeping program\'s change was still not on the disk after 15 seconds')
    aeros.quit()

    # a program waiting for a key: written while it waits
    aeros = t.boot(disk=disk)
    aeros.login()
    aeros.ask('asker')
    allow_writing(aeros)
    aeros.wait_for('Anything?', 60)
    if not wait_for_disk_file(disk, '/home/tester/asked.txt', b'written before asking'):
        aeros.fail('a program\'s change was not on the disk while it waited for a key')
    aeros.quit()
    t.check_disk(disk)


# 6 MB: more than the cache's 4 MB, so sectors have to be thrown out (and waiting ones written)
# part way through, and reading it back has to go to the disk for some of it
BLOCKS = 6144


def big_text():
    """What big.aer writes: BLOCKS lines of 1 KB, each starting with its own number, so a
    sector in the wrong place would show."""
    return ''.join((f'block {i} ' + '.' * 1024)[:1023] + '\n' for i in range(1, BLOCKS + 1))


def test_more_than_the_cache_holds(t):
    disk = t.disk(size_mb=64, fat32=True, files={
        '/bin/big.aer': program(t, 'big.aer',
                                'let dots = "."\n'
                                'while len(dots) < 1024 do\n'
                                '    dots = dots + dots\n'
                                'end\n'
                                'let blocks = []\n'
                                f'for i = 1 to {BLOCKS} do\n'
                                '    push(blocks, sub("block " + i + " " + dots, 0, 1023) + chr(10))\n'
                                'end\n'
                                'let text = join(blocks, "")\n'
                                'wrf("/home/tester/big.txt", text)\n'
                                'print("same: " + str(rdf("/home/tester/big.txt") == text))\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.cls()
    aeros.ask('big')
    allow_writing(aeros)
    aeros.wait_for('same:', 300)
    aeros.expect_line('same: true')

    # SYNC: nothing waiting any more (the prompt wrote it), and the cache is 4 MB
    aeros.cls()
    aeros.run('sync')
    aeros.expect_line('Nothing was waiting: every change is already on the disk.')
    aeros.expect('Disk cache: 4096 KB', 'Reads found in memory:')
    aeros.quit()

    # and from outside, every byte where it should be
    if read_disk_file(disk, '/home/tester/big.txt') != big_text().encode():
        aeros.fail('big.txt is wrong on the disk')
    t.check_disk(disk)
