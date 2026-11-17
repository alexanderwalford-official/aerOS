"""TCP listening (src/sys/tcp.c, LISTENING in tcp.h): other computers connecting to AerOS, and
the webserve app (src/disk/bin/webserve.aer) answering them.

QEMU forwards a port on this computer to AerOS's port 8080 (hostfwd), so connecting to
127.0.0.1:<port> here reaches whatever listens on 8080 in AerOS. To AerOS, the connection comes
from 10.0.2.2, as everything from this computer does."""

import re
import socket
import socketserver
import threading
import time

from aeros import free_port


def fetch(port, path, timeout=20):
    """GET a path from AerOS's web server, the way a browser would (but with no help: a path with
    ".." in it is sent just as it is). The status line and the body, or None if no answer came."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
            s.sendall(f'GET {path} HTTP/1.0\r\nHost: aeros\r\n\r\n'.encode())
            data = b''
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    if not data:
        return None
    head, _, body = data.partition(b'\r\n\r\n')
    return head.split(b'\r\n')[0].decode(), body


def start(aeros, line):
    """Run a BG line, and give back the thread id it says the program is on."""
    aeros.cls()
    aeros.run(line)
    match = re.search(r'as thread (\d+)', aeros.text())
    if match is None:
        aeros.fail(f'"{line}" did not start a program in the background')
    return int(match.group(1))


def test_a_web_server(t):
    port = free_port()
    aeros = t.boot(disk=t.disk(), network=f'user,model=e1000,hostfwd=tcp:127.0.0.1:{port}-:8080')
    aeros.first_login()

    # without a serve rule, nothing may listen: webserve says what would allow it
    start(aeros, 'bg webserve 8080 /docs/')
    aeros.wait_for('FW SERVE 8080 allows', 30)

    # FW SERVE: anyone may connect to port 8080
    aeros.cls()
    aeros.run('fw serve 8080')
    aeros.expect('Allowed other computers to connect to AerOS')
    aeros.cls()
    aeros.run('fw')
    aeros.expect('anyone', 'serve')

    # SCAN says it is a server
    aeros.cls()
    aeros.run('scan webserve')
    aeros.expect('let other computers connect to it')

    # serving the manual, in the background
    thread = start(aeros, 'bg webserve 8080 /docs/')
    aeros.wait_for('[webserve] Serving /docs/ on port 8080.', 30)

    # a file, byte for byte, then the list of files (there's no index.html), and a missing one
    page = fetch(port, '/index.md')
    expected = open(t.repo('src', 'disk', 'docs', 'index.md'), 'rb').read()
    if page is None or page[0] != 'HTTP/1.0 200 OK' or page[1] != expected:
        aeros.fail(f'/index.md did not come back whole: {page and page[0]}')
    page = fetch(port, '/')
    if page is None or b'index.md' not in page[1]:
        aeros.fail('/ did not list the files')
    page = fetch(port, '/nothere.md')
    if page is None or not page[0].startswith('HTTP/1.0 404'):
        aeros.fail(f'a missing file was not 404: {page and page[0]}')

    # ".." can't climb out of the folder
    page = fetch(port, '/../acc/firewall')
    if page is None or not page[0].startswith('HTTP/1.0 403'):
        aeros.fail(f'a path with .. was not refused: {page and page[0]}')

    # several visitors, one after another
    for _ in range(5):
        page = fetch(port, '/index.md')
        if page is None or page[1] != expected:
            aeros.fail('a later visit did not get the page')

    # the prompt still answers while it serves, and each request has shown up there (the serial
    # port keeps everything shown, even what has since been cleared off the screen)
    began = time.time()
    aeros.cls()
    aeros.run('ech still here')
    if time.time() - began > 5:
        aeros.fail('the prompt was held up by the server')
    for line in ('[webserve] GET /index.md 200', '[webserve] GET / 200',
                 '[webserve] GET /nothere.md 404', '[webserve] GET /../acc/firewall 403'):
        if line not in aeros.serial():
            aeros.fail(f'"{line}" was not shown at the prompt')

    # FW DEL takes the rule away: nobody gets in any more
    aeros.cls()
    aeros.run('fw del 1')
    if fetch(port, '/index.md', timeout=8) is not None:
        aeros.fail('a visitor got in with no serve rule')

    # STOP stops the server
    aeros.run(f'stop {thread}')
    aeros.wait_for('[webserve] ended', 30)


class Echo(socketserver.BaseRequestHandler):
    """A server on this computer that answers "ping" with "pong"."""

    def handle(self):
        if self.request.recv(100) == b'ping':
            self.request.sendall(b'pong')


def test_tcp_from_aerlang(t):
    # tcpconnect, tcpsend and tcprecv, to a server on this computer (a web rule allows it), and
    # tcplisten and tcpaccept with a program of its own, answering this computer
    echo_port = free_port()
    echo = socketserver.ThreadingTCPServer(('127.0.0.1', echo_port), Echo)
    threading.Thread(target=echo.serve_forever, daemon=True).start()
    port = free_port()
    try:
        program = t.path('shout.aer')
        with open(program, 'w') as f:
            # takes one visitor, and sends back what it said in capitals
            f.write('let listener = tcplisten(9000)\n'
                    'let c = tcpaccept(listener, 60000)\n'
                    'let said = tcprecv(c, 10000)\n'
                    'tcpsend(c, upper(said))\n'
                    'tcpclose(c)\n'
                    'print("shouted " + said)\n')
        aeros = t.boot(disk=t.disk(files={'/bin/shout.aer': program}),
                       network=f'user,model=e1000,hostfwd=tcp:127.0.0.1:{port}-:9000')
        aeros.first_login()

        # out: a web rule lets AerOS connect to the echo server
        aeros.run(f'fw web 10.0.2.2 {echo_port}')
        aeros.run(f'let c = tcpconnect("10.0.2.2", {echo_port})')
        aeros.run('tcpsend(c, "ping")')
        aeros.cls()
        aeros.run('tcprecv(c, 10000)')
        aeros.expect_line('pong')
        aeros.run('tcpclose(c)')

        # in: a program listens on 9000, and this computer connects to it
        aeros.run('fw serve 9000')
        start(aeros, 'bg shout')
        time.sleep(2)
        with socket.create_connection(('127.0.0.1', port), timeout=20) as s:
            s.sendall(b'hello aeros')
            reply = s.recv(100)
        if reply != b'HELLO AEROS':
            aeros.fail(f'the program in AerOS answered {reply!r}')
        aeros.wait_for('[shout] shouted hello aeros', 30)
    finally:
        echo.shutdown()
