"""Programs in the background (BG, src/sys/jobs.c): AerLang programs on threads of their own,
while the prompt carries on."""

import http.server
import re
import threading
import time

from aeros import free_port, read_disk_file


def program(t, name, source):
    """Write an AerLang program to this test's folder, for t.disk(files=...)."""
    path = t.path(name)
    with open(path, 'w') as f:
        f.write(source)
    return path


def start(aeros, line):
    """Run a BG line, and give back the thread id it says the program is on."""
    aeros.cls()
    aeros.run(line)
    match = re.search(r'as thread (\d+)', aeros.text())
    if match is None:
        aeros.fail(f'"{line}" did not start a program in the background')
    return int(match.group(1))


def test_a_program_runs_while_the_prompt_carries_on(t):
    disk = t.disk(files={'/bin/counter.aer': program(t, 'counter.aer',
                         'for i = 1 to 5 do\n'
                         '    print("tick " + i)\n'
                         '    sleep(400)\n'
                         'end\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    start(aeros, 'bg counter')

    # the prompt works while it runs: AerLang, and a command
    aeros.run('6 * 7')
    aeros.expect_line('42')
    aeros.run('ech still here')
    aeros.expect_line('still here')

    # what it printed shows up at the empty prompt, each line marked with its name, in order,
    # then its last line
    aeros.wait_for('[counter] finished', 30)
    shown = aeros.serial()
    ticks = [shown.find(f'[counter] tick {i}') for i in range(1, 6)]
    if -1 in ticks or ticks != sorted(ticks):
        aeros.fail(f'the ticks were not all shown, in order: {ticks}')


def test_list_and_stop(t):
    disk = t.disk(files={'/bin/forever.aer': program(t, 'forever.aer',
                         'while true do\n'
                         '    sleep(100)\n'
                         'end\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    thread = start(aeros, 'bg forever')

    # BG lists it, and THR shows its thread
    aeros.cls()
    aeros.run('bg')
    aeros.expect('forever', 'STOP <id> stops one.')
    aeros.cls()
    aeros.run('thr')
    aeros.expect('forever', 'of 262144 bytes')

    # STOP: it stops at its next safe point, and says so
    aeros.run(f'stop {thread}')
    aeros.wait_for('stopped (STOP)', 30)
    aeros.wait_for('[forever] ended', 30)
    aeros.cls()
    aeros.run('bg')
    aeros.expect('Nothing is running in the background.')

    # Ctrl+C at the prompt is for the prompt, never a program in the background
    start(aeros, 'bg forever')
    before = aeros.prompts()
    aeros.key('ctrl-c')
    time.sleep(1)
    aeros.cls()
    aeros.run('bg')
    aeros.expect('forever')


def test_the_keyboard_and_screen_are_for_the_front(t):
    disk = t.disk(files={'/bin/asker.aer': program(t, 'asker.aer',
                         'let name = input("Name? ")\n'
                         'print("hello " + name)\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    start(aeros, 'bg asker')
    aeros.wait_for('[asker] ended', 30)
    aeros.expect('input() is for the program in front')

    # and a program that isn't there is caught straight away
    aeros.cls()
    aeros.run('bg nothere')
    aeros.expect('There is no program called nothere.')


def test_files_need_an_always(t):
    disk = t.disk(files={'/bin/saver.aer': program(t, 'saver.aer',
                         'print(wrf("saved.txt", "from the background"))\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # in the background it can't ask, so changing a file in your home folder is refused (wrf
    # gives false, and the program carries on to its end)
    start(aeros, 'bg saver')
    aeros.wait_for('[saver] finished', 30)
    aeros.expect("where it can't ask", '[saver] false')
    aeros.cls()
    aeros.run('isf("saved.txt")')
    aeros.expect_line('false')

    # once it has been run in front and answered A (always), it may
    aeros.ask('saver')
    aeros.wait_for('wants to change', 60)
    aeros.key('a')
    aeros.wait_for('true', 30)
    aeros.run('delf("saved.txt")')
    start(aeros, 'bg saver')
    aeros.wait_for('[saver] finished', 30)
    aeros.expect('[saver] true')

    # in the folder it was started in: changing folder at the prompt doesn't move it
    aeros.run('delf("saved.txt")')
    start(aeros, 'bg saver')
    aeros.run('sd /')
    aeros.wait_for('[saver] finished', 30)
    aeros.quit()
    if read_disk_file(disk, '/home/tester/saved.txt') != b'from the background':
        aeros.fail('the program in the background did not write in its own folder')
    t.check_disk(disk)


def test_a_busy_program_does_not_hold_up_the_prompt(t):
    # busy for 20 seconds without ever sleeping, with a global of its own called x
    disk = t.disk(files={'/bin/busy.aer': program(t, 'busy.aer',
                         'let x = 99\n'
                         'let start = time()\n'
                         'while time() - start < 20000 do\n'
                         'end\n'
                         'print("x is " + x)\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    aeros.run('let x = 5')
    start(aeros, 'bg busy')

    # the prompt still answers quickly: it gets turns between the loop's rounds
    began = time.time()
    aeros.run('ech quick')
    if time.time() - began > 5:
        aeros.fail('the prompt was held up by the program in the background')

    # and the prompt's x and the program's x are different variables
    aeros.run('x')
    aeros.expect_line('5')
    aeros.wait_for('[busy] x is 99', 60)


def test_in_front_and_behind_at_once(t):
    # keeper adds a row to its own database every 50 ms, forever. front opens and closes a
    # database of its own, and ends: that must not close keeper's
    disk = t.disk(files={
        '/bin/keeper.aer': program(t, 'keeper.aer',
                                   'let db = dbopen("k.db")\n'
                                   'dbrun(db, "CREATE TABLE IF NOT EXISTS t (n INTEGER)")\n'
                                   'let i = 0\n'
                                   'while true do\n'
                                   '    i = i + 1\n'
                                   '    dbrun(db, "INSERT INTO t VALUES (?)", [i])\n'
                                   '    sleep(50)\n'
                                   'end\n'),
        '/bin/front.aer': program(t, 'front.aer',
                                  'let db = dbopen("f.db")\n'
                                  'dbrun(db, "CREATE TABLE IF NOT EXISTS t (n INTEGER)")\n'
                                  'sleep(1000)\n'
                                  'print("front done")\n'),
        '/bin/spin.aer': program(t, 'spin.aer', 'while true do\nend\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()

    # keeper in front first, to answer A (always) for its folder; Ctrl+C stops it
    before = aeros.prompts()
    aeros.ask('keeper')
    aeros.wait_for('wants to change', 60)
    aeros.key('a')
    time.sleep(1)
    aeros.key('ctrl-c')
    aeros.wait_prompt(before, 30, 'keeper after Ctrl+C')

    keeper = start(aeros, 'bg keeper')
    time.sleep(1)

    # the prompt can't open keeper's database while keeper has it
    aeros.cls()
    aeros.run('dbopen("k.db")')
    aeros.expect('is open in another program')

    # a program in front, with a database of its own, runs and ends alongside it
    aeros.ask('front')
    aeros.wait_for('wants to change', 60)
    aeros.key('a')
    aeros.wait_for('front done', 30)

    # Ctrl+C stops the program in front, and leaves keeper running
    before = aeros.prompts()
    aeros.ask('spin')
    time.sleep(1)
    aeros.key('ctrl-c')
    aeros.wait_prompt(before, 30, 'spin after Ctrl+C')
    aeros.cls()
    aeros.run('bg')
    aeros.expect('keeper')

    # keeper kept adding rows all along (its database wasn't closed under it): stop it, and count
    time.sleep(1)
    aeros.run(f'stop {keeper}')
    aeros.wait_for('[keeper] ended', 30)
    aeros.cls()
    aeros.run('let d = dbopen("k.db")')
    aeros.run('len(dbquery(d, "SELECT n FROM t")) > 40')
    aeros.expect_line('true')


class SlowPages(http.server.BaseHTTPRequestHandler):
    """A web server that takes its time: /slow answers after 6 seconds, /hello at once."""
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        if self.path == '/slow':
            time.sleep(6)
            body = b'slow page\n'
        else:
            body = b'quick page\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # quiet


def test_fetching_a_page_does_not_hold_up_the_prompt(t):
    # httpget in the background waits for the server with the network lock held (src/sys/net.c),
    # but lets the other threads run meanwhile
    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), SlowPages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f'http://10.0.2.2:{port}'
    try:
        disk = t.disk(files={'/bin/fetcher.aer': program(t, 'fetcher.aer',
                             'let page = httpget(args[0])\n'
                             'print("got " + page)\n')})
        aeros = t.boot(disk=disk, network='user,model=e1000')
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        start(aeros, f'bg fetcher {site}/slow')
        time.sleep(1)

        # the prompt answers straight away, while the page is still on its way
        began = time.time()
        aeros.run('ech quick')
        if time.time() - began > 3:
            aeros.fail('the prompt was held up while a program in the background fetched a page')

        # a page fetched at the prompt meanwhile waits its turn for the network, then comes
        # back whole, and so does the slow one
        aeros.cls()
        aeros.run(f'get {site}/hello', timeout=90)
        aeros.expect('quick page')
        aeros.wait_for('[fetcher] got slow page', 60)
    finally:
        server.shutdown()


def test_logging_out_stops_them(t):
    disk = t.disk(files={'/bin/forever.aer': program(t, 'forever.aer',
                         'while true do\n'
                         '    sleep(100)\n'
                         'end\n')})
    aeros = t.boot(disk=disk)
    aeros.first_login()
    start(aeros, 'bg forever')
    aeros.ask('out')
    aeros.login()
    aeros.cls()
    aeros.run('bg')
    aeros.expect('Nothing is running in the background.')
