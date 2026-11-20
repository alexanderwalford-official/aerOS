"""startup.txt (src/sys/startup.h): each user's own commands, run each time they log in, as if
typed at the prompt; what it won't run; and that no program can change it."""

from aeros import read_disk_file

STARTUP = '''# a comment, skipped
print("hello from startup")

mkf made.txt made by startup
rbt
ech after the skipped line
'''


def test_a_new_account_gets_an_example(t):
    # the example is all comments: nothing runs until a # is taken off
    disk = t.disk()
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.expect_not('Running startup.txt')
    aeros.quit()
    example = read_disk_file(disk, '/home/tester/startup.txt')
    if example is None or not example.startswith(b'# startup.txt'):
        aeros.fail(f'a new home folder has no example startup.txt: {example!r}')
    if any(line and not line.startswith(b'#') for line in example.split(b'\n')):
        aeros.fail('the example startup.txt has a line that would run')


def test_startup_runs_at_each_login(t):
    startup = t.path('startup.txt')
    with open(startup, 'w') as f:
        f.write(STARTUP)
    sneaky = t.path('sneaky.aer')
    with open(sneaky, 'w') as f:
        # a program trying to give itself a line in startup.txt
        f.write('print(wrf("/home/tester/startup.txt", "del /home/tester/"))\n')
    disk = t.disk(files={'/home/tester/startup.txt': startup, '/bin/sneaky.aer': sneaky})
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # each line is shown, then run; rbt is skipped (it would restart for ever), and the rest
    # carries on after it
    aeros.expect('Running startup.txt', '> print("hello from startup")',
                 "Skipped: startup.txt can't restart")
    aeros.expect_line('hello from startup', 'after the skipped line')

    # a program can't change it, and isn't even asked about it (it would only be refused)
    asked = aeros.serial().count('[ ? ]')
    aeros.cls()
    aeros.run('sneaky')
    aeros.expect_line('false')
    if aeros.serial().count('[ ? ]') != asked:
        aeros.fail('aerOS asked about a program changing startup.txt, rather than refusing it')

    # it runs again at the next login
    aeros.ask('out')
    aeros.login()
    if aeros.serial().count('hello from startup') < 4:   # shown, and printed, each time
        aeros.fail('startup.txt did not run again at the next login')
    aeros.quit()
    if read_disk_file(disk, '/home/tester/made.txt') is None:
        aeros.fail('the mkf line in startup.txt did not make its file')
    with open(startup, 'rb') as f:
        if read_disk_file(disk, '/home/tester/startup.txt') != f.read():
            aeros.fail('startup.txt was changed')
