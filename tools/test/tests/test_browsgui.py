"""The graphical web browser package (packages/browsgui.aer), on the graphics screen.

A web server on this computer serves a page with a heading, a link and two pictures. The test
starts the browser on it and watches what the server is asked for (the page, then the pictures),
looks at the colours on the screen, clicks the link with the mouse, goes back with Backspace,
types an address in the address bar, and quits with Esc."""

import http.server
import struct
import threading
import time
import zlib

from aeros import free_port

RED = (200, 30, 30)
BLUE = (30, 30, 200)
LINK = (0, 0, 204)       # the colour the browser draws links in (C_LINK)
PAGE = b"""<html><head><title>Test page</title></head><body>
<h1>Big heading</h1>
<p>A paragraph with <b>bold words</b>, a <a href="/two.html">link to page two</a> and more words.</p>
<p><img src="/red.png" alt="a red box"> and <img src="/blue.png" alt="blue"> in a line of words</p>
""" + b''.join(b'<p>Filler paragraph %d to make the page long enough to scroll.</p>\n' % i for i in range(40)) + b'</body></html>'
TWO = b'<title>Two</title><h1>Page two</h1><p>You followed the link.</p>'


def make_png(width, height, colour):
    """A plain PNG of one colour."""
    rows = b''.join(b'\x00' + bytes(colour) * width for _ in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def count_colour(shot, colour):
    """How many pixels of the screenshot are exactly this colour."""
    width, height, pixels = shot
    wanted = bytes(colour)
    return sum(1 for i in range(0, len(pixels), 3) if pixels[i:i + 3] == wanted)


def test_browsgui(t):
    asked = []                       # every path the server was asked for, in order
    pictures = {'/red.png': make_png(120, 60, RED), '/blue.png': make_png(80, 80, BLUE)}

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            if self.path in pictures:
                body, kind = pictures[self.path], 'image/png'
            elif self.path == '/two.html':
                body, kind = TWO, 'text/html'
            else:
                body, kind = PAGE, 'text/html'
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait_asked(path, count, what):
        """Wait until the server has been asked for path this many times."""
        for _ in range(300):
            if asked.count(path) >= count:
                return
            time.sleep(0.1)
        aeros.fail(f'the browser never asked for {what} ({path}); the server was asked for {asked}')

    def move_pointer(dx, dy):
        """Move the mouse in steps (QEMU sends a big move in pieces of a hundred or so)."""
        while dx or dy:
            step_x = max(-100, min(100, dx))
            step_y = max(-100, min(100, dy))
            aeros.mouse_move(step_x, step_y)
            dx -= step_x
            dy -= step_y
            time.sleep(0.05)
        time.sleep(0.5)

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('browsgui'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        before = aeros.prompts()
        aeros.ask(f'browsgui http://10.0.2.2:{port}/')

        # the page, then the pictures on it (the browser fetches those once the page is up)
        wait_asked('/', 1, 'the page')
        wait_asked('/red.png', 1, 'the first picture')
        wait_asked('/blue.png', 1, 'the second picture')
        time.sleep(1.5)
        shot = aeros.screenshot('page')
        # the pictures are drawn in their places (120 x 60 and 80 x 80 pixels), and the link is in
        # its colour: underlined text, so many pixels
        if count_colour(shot, RED) < 120 * 60 // 2:
            aeros.fail('the red picture is not on the screen')
        if count_colour(shot, BLUE) < 80 * 80 // 2:
            aeros.fail('the blue picture is not on the screen')
        if count_colour(shot, LINK) < 100:
            aeros.fail('the link is not drawn in the link colour')

        # click the link: the pointer starts in the middle of the screen (512, 384), and the link's
        # words are near x 280, y 100 on a 1024 x 768 screen
        move_pointer(-230, -284)
        aeros.mouse_buttons(1)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        wait_asked('/two.html', 1, 'the page the link goes to')
        time.sleep(1)
        # the new page has no pictures: none of the red is left
        if count_colour(aeros.screenshot('two'), RED) != 0:
            aeros.fail('the page the link goes to is not showing')

        # Backspace: back to the first page, which is fetched again (with its pictures)
        aeros.key('backspace')
        wait_asked('/', 2, 'the first page again, after Backspace')
        wait_asked('/blue.png', 2, 'its pictures again')
        time.sleep(1.5)
        if count_colour(aeros.screenshot('back'), RED) < 120 * 60 // 2:
            aeros.fail('going back did not bring the first page back')

        # Tab to the address bar, type an address, Enter
        aeros.key('tab')
        time.sleep(0.5)
        aeros.type(f'http://10.0.2.2:{port}/two.html\n')
        wait_asked('/two.html', 2, 'the address typed in the address bar')

        # Esc quits, back to the prompt
        time.sleep(1)
        aeros.key('esc')
        aeros.wait_prompt(before, 30, what='browsgui')
    finally:
        server.shutdown()
