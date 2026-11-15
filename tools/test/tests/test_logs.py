"""AerOS's log, /sys/logs.db (src/sys/syslog.h): boots, entries, noticing a boot that didn't shut
down properly, and that only AerOS can change it."""

import time

from aeros import GUEST_PROMPT

LOG = 'sql /sys/logs.db '


def shut_down(aeros):
    """OFF, then wait for QEMU to stop (AerOS turns the emulated computer off)."""
    aeros.ask('off')
    start = time.time()
    while aeros.proc.poll() is None:
        if time.time() - start > 30:
            aeros.fail('OFF did not turn the computer off')
        time.sleep(0.1)
    aeros.quit()


def test_boots(t):
    disk = t.disk()

    # the first boot: boot 1, still running
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.cls()
    aeros.run(LOG + 'SELECT boot, ended, clean FROM boots')
    aeros.expect_line('boot | ended | clean', '1 | NULL | NULL', '(1 row)')
    # the boot log's lines are in it: a [WARN] as a warning
    aeros.cls()
    aeros.run(LOG + "SELECT kind, source FROM log WHERE source = 'sound'")
    aeros.expect_line('warning | sound')
    aeros.cls()
    aeros.run(LOG + "SELECT kind, message FROM log WHERE source = 'disk'")
    aeros.expect_line('boot | AEROS disk found: files are saved on it')
    shut_down(aeros)
    t.check_disk(disk)

    # OFF ended boot 1 properly
    aeros = t.boot(disk=disk)
    aeros.login()
    aeros.cls()
    aeros.run(LOG + 'SELECT boot, clean FROM boots')
    aeros.expect_line('1 | 1', '2 | NULL', '(2 rows)')
    aeros.cls()
    aeros.run(LOG + "SELECT boot, kind, message FROM log WHERE kind = 'shutdown'")
    aeros.expect_line('1 | shutdown | shutting down (OFF)')
    # this time the computer just stops, as if the power went
    aeros.quit()

    # so the next boot says boot 2 didn't end properly, and marks it
    aeros = t.boot(disk=disk)
    aeros.login()
    aeros.expect_printed("[WARN] AerOS didn't shut down properly last time (boot 2, started 20")
    aeros.cls()
    aeros.run(LOG + 'SELECT boot, clean FROM boots')
    aeros.expect_line('1 | 1', '2 | 0', '3 | NULL', '(3 rows)')
    aeros.cls()
    aeros.run(LOG + "SELECT boot, kind, source FROM log WHERE kind = 'warning' AND source = 'power'")
    aeros.expect_line('3 | warning | power')


def test_only_aeros_changes_it(t):
    disk = t.disk(files={'/bin/logs.aer': t.files + '/logs.aer'})
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # a program reads it without being asked, can't change it, and can add to it with logadd
    aeros.cls()
    aeros.run('logs')
    printed = aeros.serial()
    failures = [line for line in printed.split('\n') if line.startswith('FAIL')]
    if failures or 'ALL PASSED' not in printed:
        aeros.fail('logs.aer failed:\n' + '\n'.join(failures))
    if '[ ? ]' in printed:
        aeros.fail('reading the log asked for permission')
    aeros.cls()
    aeros.run(LOG + "SELECT kind, source, message FROM log WHERE kind = 'app'")
    aeros.expect_line('app | logs | hello from the test', '(1 row)')

    # from the terminal it can be read, but not changed or deleted
    aeros.cls()
    aeros.run(LOG + 'DELETE FROM log')
    aeros.run(LOG + "INSERT INTO boots VALUES (99, 'x', 'x', 1)")
    aeros.expect('Error: logs.db was opened only to read: it can\'t be changed')
    aeros.cls()
    aeros.run('del /sys/logs.db')
    aeros.run('wr /sys/logs.db nothing')
    aeros.expect("Can't delete /sys/logs.db: it can be read but not changed",
                 'Could not write /sys/logs.db: it can be read but not changed')
    aeros.cls()
    aeros.run('why')
    aeros.expect('/sys/logs.db (AerOS\'s log)')
    aeros.cls()
    aeros.run(LOG + 'SELECT boot FROM boots')
    aeros.expect_line('1', '(1 row)')

    # code at the prompt opening it to read can't change it either
    aeros.cls()
    aeros.run('let d = dbopen("/sys/logs.db", "read")')
    aeros.run('dbrun(d, "DELETE FROM log")')
    aeros.expect("logs.db was opened only to read")
    aeros.quit()
    t.check_disk(disk)


def test_in_memory(t):
    # with no disk the log is kept in memory, like every file
    aeros = t.boot()
    aeros.wait_for(GUEST_PROMPT, 60)
    aeros.cls()
    aeros.run(LOG + "SELECT kind, message FROM log WHERE source = 'disk'")
    aeros.expect_line('boot | no AEROS disk found: files only last until the computer is turned off')


def test_clearing(t):
    # logclear(): AerOS asks Y / N, then the password, and only then clears it
    disk = t.disk()
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # N: nothing happens
    aeros.cls()
    aeros.ask('logclear()')
    aeros.wait_for("[ ? ] Clear AerOS's log", 30)
    before = aeros.prompts()
    aeros.key('n')
    aeros.wait_prompt(before)
    aeros.expect_line('false')

    # A isn't an answer here (it is asked every time), then Y and a wrong password: nothing
    # happens, and the try is logged
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('logclear()')
    aeros.wait_for('[ ? ]', 30)
    aeros.key('a')
    aeros.key('y')
    aeros.answer('wrong1')
    aeros.wait_prompt(before)
    aeros.expect('-> yes', 'Wrong password: nothing was changed.')
    aeros.expect_line('false')
    aeros.cls()
    aeros.run(LOG + "SELECT message FROM log WHERE source = 'log'")
    aeros.expect("a wrong password was typed for tester, trying to clear AerOS's log")

    # not while the log is open: clearing it moves its pages
    aeros.cls()
    aeros.run('let d = dbopen("/sys/logs.db", "read")')
    aeros.run('logclear()')
    aeros.expect('close the log with dbclose() first')
    aeros.run('dbclose(d)')

    # Y and the right password: cleared, leaving this boot and one entry saying who did it
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('logclear()')
    aeros.wait_for('[ ? ]', 30)
    aeros.key('y')
    aeros.answer('secret1')
    aeros.wait_prompt(before)
    aeros.expect_line('true')
    aeros.cls()
    aeros.run(LOG + 'SELECT kind, message FROM log')
    aeros.expect_line('warning | the log was cleared by tester, with the prompt', '(1 row)')
    aeros.cls()
    aeros.run(LOG + 'SELECT boot FROM boots')
    aeros.expect_line('1', '(1 row)')

    # from a program, the question says which program wants it
    aeros.run('mkf clr.aer print(logclear())')
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('clr')
    aeros.wait_for("[ ? ] clr.aer wants to clear AerOS's log", 30)
    aeros.key('n')
    aeros.wait_prompt(before)
    aeros.expect_line('false')
    aeros.quit()
    t.check_disk(disk)


def test_turning_logging_off(t):
    # setlogging(false): AerOS asks Y / N and the password, then nothing is logged (even after a
    # restart) until setlogging(true)
    disk = t.disk()
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.cls()
    aeros.run('islogging()')
    aeros.expect_line('true')

    # N: still on
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('setlogging(false)')
    aeros.wait_for("[ ? ] Turn AerOS's logging off?", 30)
    aeros.key('n')
    aeros.wait_prompt(before)
    aeros.expect_line('false')

    # Y and the password: off, and that much is logged
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('setlogging(false)')
    aeros.wait_for('[ ? ]', 30)
    aeros.key('y')
    aeros.answer('secret1')
    aeros.wait_prompt(before)
    aeros.expect_line('true')
    aeros.run('logadd("not kept")')
    aeros.cls()
    aeros.run('islogging()')
    aeros.expect_line('false')
    aeros.cls()
    aeros.run(LOG + "SELECT message FROM log WHERE kind = 'app'")
    aeros.expect_line('(0 rows)')
    aeros.cls()
    aeros.run(LOG + "SELECT message FROM log WHERE source = 'log'")
    aeros.expect_line('logging was turned off by tester, with the prompt')
    aeros.quit()

    # still off after a restart: the boot is listed (so crashes are still noticed), but nothing
    # it did is kept
    aeros = t.boot(disk=disk)
    aeros.login()
    aeros.cls()
    aeros.run(LOG + 'SELECT boot FROM boots')
    aeros.expect_line('1', '2', '(2 rows)')
    aeros.cls()
    aeros.run(LOG + 'SELECT kind FROM log WHERE boot = 2')
    aeros.expect_line('(0 rows)')

    # back on: kept again
    aeros.cls()
    before = aeros.prompts()
    aeros.ask('setlogging(true)')
    aeros.wait_for("[ ? ] Turn AerOS's logging back on?", 30)
    aeros.key('y')
    aeros.answer('secret1')
    aeros.wait_prompt(before)
    aeros.expect_line('true')
    aeros.run('logadd("kept again")')
    aeros.cls()
    aeros.run(LOG + 'SELECT source, message FROM log WHERE boot = 2')
    aeros.expect_line('log | logging was turned on by tester, with the prompt', 'prompt | kept again', '(2 rows)')
    aeros.quit()
    t.check_disk(disk)
