"""TCP and HTTP: GET and httpget() fetching pages from a web server on this computer (src/sys/tcp.c,
src/sys/http.c).

QEMU's user networking puts this computer at 10.0.2.2 as AerOS sees it, so a web server here on
127.0.0.1:<port> is http://10.0.2.2:<port>/ to AerOS."""

import http.server
import threading

from aeros import GUEST_PROMPT, free_port

HELLO = b'Hello from the web!\n'
# big enough to need many TCP segments, and several fills of AerOS's 16 KB buffer
BIG = b''.join(b'line %05d of the big file\n' % i for i in range(8000))
# more than the 4 MB AerOS used to fetch at most (6 MB, 32 bytes a line), and more than the 16 MB
# it fetches now (src/sys/http.h)
HUGE = b''.join(b'line %07d of the huge file..\n' % i for i in range(6 * 1024 * 1024 // 32))
TOO_BIG = b'x' * (17 * 1024 * 1024)


class Pages(http.server.BaseHTTPRequestHandler):
    """The pages the tests fetch."""
    protocol_version = 'HTTP/1.1'

    def send(self, status, body, content_type='text/plain', headers=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/hello.txt':
            self.send(200, HELLO)
        elif self.path == '/big.txt':
            self.send(200, BIG)
        elif self.path == '/huge.txt':
            self.send(200, HUGE)
        elif self.path == '/toobig.txt':
            self.send(200, TOO_BIG)
        elif self.path == '/moved':
            self.send(302, b'', headers=[('Location', '/hello.txt')])
        elif self.path == '/forbidden':
            self.send(403, b'<title>No</title><h1>Access denied</h1><p>Robots are not welcome here.</p>', 'text/html')
        elif self.path == '/chunked':
            # the body in pieces, each with its length in hex first, then a 0 length piece
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Connection', 'close')
            self.end_headers()
            for piece in (b'Sent ', b'in three ', b'pieces\n'):
                self.wfile.write(b'%x\r\n%s\r\n' % (len(piece), piece))
            self.wfile.write(b'0\r\n\r\n')
        else:
            self.send(404, b'No such page\n')

    def log_message(self, *args):
        pass  # quiet


def test_get_and_httpget(t):
    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f'http://10.0.2.2:{port}'
    try:
        aeros = t.boot(network='user,model=e1000')
        aeros.wait_for(GUEST_PROMPT, 60)

        # nothing can be fetched until a web rule allows the computer and port
        aeros.cls()
        aeros.run(f'get {site}/hello.txt')
        aeros.expect(f'The firewall has no web rule for 10.0.2.2 port {port}',
                     f'FW WEB 10.0.2.2 {port} allows fetching from it.')
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.expect(f'Allowed web pages from 10.0.2.2 port {port}')
        aeros.cls()
        aeros.run('fw')
        rule = [row for row in aeros.text().split('\n') if f'port {port}' in row]
        if not rule or rule[0].split()[-1] != 'web':
            aeros.fail('FW should list the rule as a web rule')

        # a page, shown
        aeros.cls()
        aeros.run(f'get {site}/hello.txt', timeout=90)
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello from the web!')

        # a big one, saved in a file: every byte arrives, in order
        aeros.cls()
        aeros.run(f'get {site}/big.txt big.txt', timeout=120)
        aeros.expect_line(f'200 OK, {len(BIG)} bytes, text/plain', 'Saved in big.txt.')
        aeros.cls()
        aeros.run('len(rdf("big.txt"))')
        aeros.expect_line(str(len(BIG)))
        aeros.run('let lines = split(rdf("big.txt"), chr(10))')
        aeros.run('print(lines[4000], lines[7999])')
        aeros.expect_line('line 04000 of the big file line 07999 of the big file')

        # bigger than the old 4 MB limit, every byte of it; but not more than 16 MB
        aeros.cls()
        aeros.run(f'let huge = httpget("{site}/huge.txt")', timeout=180)
        aeros.run('print(len(huge))')
        aeros.expect_line(str(len(HUGE)))
        aeros.run('print(sub(huge, 100000 * 32, 31))')
        aeros.expect_line('line 0100000 of the huge file..')
        aeros.run('print(sub(huge, len(huge) - 32, 31))')
        aeros.expect_line('line %07d of the huge file..' % (len(HUGE) // 32 - 1))
        aeros.cls()
        aeros.run(f'get {site}/toobig.txt', timeout=180)
        # (the message is wider than the screen: "(16 MB)" wraps onto the next row)
        aeros.expect('it is bigger than AerOS will fetch', '(16 MB)')

        # in pieces, moved somewhere else, and not there at all
        aeros.cls()
        aeros.run(f'get {site}/chunked', timeout=90)
        aeros.expect_line('200 OK, 21 bytes, text/plain', 'Sent in three pieces')
        aeros.cls()
        aeros.run(f'get {site}/moved', timeout=90)
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello from the web!')
        aeros.cls()
        aeros.run(f'get {site}/nothing', timeout=90)
        aeros.expect_line('404 Not Found, 13 bytes, text/plain', 'No such page')

        # a page that says no: httpget gives nil, httpwhy says what the answer was, and httpbody has the
        # site's own page for it
        aeros.cls()
        aeros.run(f'let refused = httpget("{site}/forbidden")')
        aeros.run('print(refused == nil, httpwhy()[0], httpwhy()[1], httpwhy()[2])')
        aeros.expect('true 0 403 the site answered 403 Forbidden')
        aeros.run('print(httpbody())')
        aeros.expect('Access denied')

        # from AerLang: the page's text, or nil if it isn't there
        aeros.cls()
        aeros.run(f'httpget("{site}/hello.txt")', timeout=90)
        aeros.expect('Hello from the web!')
        aeros.run(f'httpget("{site}/nothing") == nil', timeout=90)
        aeros.expect_line('true')

        # a web rule only allows fetching pages: no sealed messages to that computer and port
        aeros.cls()
        aeros.run(f'msg 10.0.2.2 {port} hello')
        aeros.expect("Can't send to 10.0.2.2: the firewall blocks it")

        # https needs a web rule too, for port 443 (test_https.py has HTTPS itself)
        aeros.cls()
        aeros.run('get https://example.com/')
        aeros.expect('The firewall has no web rule for example.com port 443',
                     'FW WEB example.com 443 allows fetching from it.')
    finally:
        server.shutdown()


def test_browser_shows_a_forbidden_page(t):
    """The text browser (packages/browser.aer): a site that answers 403 has a page of its own, which is
    shown, with what the answer was underneath, instead of only "couldn't load"."""
    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('browser'))
        aeros = t.boot(network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.ask(f'browser http://10.0.2.2:{port}/forbidden')
        aeros.wait_for('Access denied', 60)
        aeros.expect('Robots are not welcome here.', 'The site answered 403 Forbidden: its own page is shown.')
    finally:
        server.shutdown()
