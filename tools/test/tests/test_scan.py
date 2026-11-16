"""SCAN: what an AerLang program can do, found by reading it before it runs (src/lang/aer_scan.c).
Its report is long, so these look in the serial log, which keeps everything printed."""


def test_scan(t):
    disk = t.disk(files={'/bin/phish.aer': t.files + '/phish.aer',
                         '/bin/clean.aer': t.files + '/clean.aer'})
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # a built-in app: Snake keeps high scores in a database and draws on the whole screen,
    # and nothing about it is suspicious
    aeros.run('scan snake')
    aeros.expect_printed('/bin/games/snake.aer can:',
                         '  use databases (dbopen, dbrun, dbquery)',
                         '  draw on the whole screen (gopen)',
                         'Nothing looks suspicious.')

    # a program that tries to fool you: each thing it can do, and the three tricks
    aeros.run('scan phish')
    aeros.expect_printed('/bin/phish.aer can:',
                         '  write files (wrf)',
                         '  delete files (delf)',
                         '  send and receive over the network (nsend)',
                         '  ask you to type a secret (inpw)',
                         '  queue other programs to run (queue)',
                         "  ! line 4: it prints something that looks like AerOS's own questions",
                         '  ! line 5: it asks for a password: only AerOS itself should ask for yours',
                         "  ! line 7: it writes programs and queues them: those can't be seen here",
                         '3 thing(s) worth a closer look: read it with RD before running it.')

    # a word in a comment or a string isn't a built-in the program uses
    aeros.run('scan clean')
    aeros.expect_printed('/bin/clean.aer only works with its own values, prints, and reads what you type.')
    if '/bin/clean.aer can:' in aeros.serial():
        aeros.fail('clean.aer only mentions delf in a comment and a string, but the scan says it can do things')

    # and a program that isn't there
    aeros.run('scan nothere')
    aeros.expect_printed('There is no program called nothere.aer here, or in the app folders.')
