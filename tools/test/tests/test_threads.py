"""Threads (src/sys/thread.c): several things in AerOS taking turns on the one processor, at
safe points."""

import time

from aeros import GUEST_PROMPT, wait_for_disk_file


def test_threads_take_turns(t):
    disk = t.disk()
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # the boot self-test: two threads took turns, slept and finished, in the right order
    if '[ OK ] Threads self-test' not in aeros.serial():
        aeros.fail('the threads self-test did not pass at boot')

    # THR: main, running the terminal, and the disk cache's writer, asleep between its looks
    aeros.cls()
    aeros.run('thr')
    aeros.expect('main', 'running', 'disk writer', 'sleeping', 'of 16384 bytes')


def test_ctrl_c_stops_an_empty_loop(t):
    # a loop with nothing inside reaches a safe point every time round (so other threads get
    # turns), and that mustn't get in the way of Ctrl+C stopping it
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 120)
    before = aeros.prompts()
    aeros.ask('while true do end')
    time.sleep(2)
    aeros.key('ctrl-c')
    aeros.wait_prompt(before, 30, 'the empty loop after Ctrl+C')


def test_the_disk_writer_runs_during_a_busy_program(t):
    # The program writes a file, then keeps the processor busy with a loop that does nothing
    # (no sleep, no key, no disk). QEMU is stopped while it is still busy, so nothing else could
    # have written the change: it is on the disk only if the disk writer thread had its turns
    # at the loop's safe points
    busy = t.path('busy.aer')
    with open(busy, 'w') as f:
        f.write('wrf("/home/tester/busy.txt", "written while busy")\n'
                'print("written")\n'
                'let start = time()\n'
                'while time() - start < 60000 do\n'
                'end\n')
    disk = t.disk(files={'/bin/busy.aer': busy})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.ask('busy')
    aeros.wait_for('wants to change', 60)
    aeros.key('a')
    aeros.wait_for('written', 60)
    # (the loop runs for a minute, so if the change gets onto the disk while it does, the disk
    # writer thread had its turns)
    if not wait_for_disk_file(disk, '/home/tester/busy.txt', b'written while busy'):
        aeros.fail('the change was not on the disk: the disk writer thread did not get a turn')
    aeros.quit()
    t.check_disk(disk)
