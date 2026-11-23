"""The graphical web browser package (packages/browsgui.aer), on the graphics screen.

A web server on this computer serves a page with a heading, a link and two pictures. The test
starts the browser on it and watches what the server is asked for (the page, then the pictures),
looks at the colours on the screen, clicks the link with the mouse, goes back with Backspace,
types an address in the address bar, and quits with Esc."""

import http.server
import struct
import subprocess
import sys
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


SUGGEST = (255, 240, 180)    # the colour of the strip that suggests allowing a site (C_SUGGEST)
QUESTION = (0xFF, 0xD0, 0x80)   # the colour of aerOS's own question box (test_desktop.py has it too)


def test_browsgui_suggests_allowing_sites(t):
    """A page whose pictures come from sites the firewall blocks: the browser offers to allow
    them, aerOS asks, and only a yes opens the firewall."""
    asked = {'page': [], 'first': [], 'second': []}

    def serve(name, handler_body):
        class Pages(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                asked[name].append(self.path)
                body, kind = handler_body(self.path)
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        port = free_port()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, port

    # two other "sites" (ports) for the pictures, found by the page's <img src>
    first, first_port = serve('first', lambda path: (make_png(120, 60, RED), 'image/png'))
    second, second_port = serve('second', lambda path: (make_png(80, 80, BLUE), 'image/png'))
    page = (b'<title>Pictures</title><h1>Pictures from elsewhere</h1><p>Words.</p>'
            b'<p><img src="http://10.0.2.2:%d/a.png" alt="red"> <img src="http://10.0.2.2:%d/b.png" alt="blue"></p>'
            % (first_port, second_port))
    site, site_port = serve('page', lambda path: (page, 'text/html'))

    def wait(condition, what, seconds=40):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what)

    try:
        disk = t.disk(files=t.package('browsgui'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {site_port}')          # the page's site only
        before = aeros.prompts()
        aeros.ask(f'browsgui http://10.0.2.2:{site_port}/')

        # the pictures are blocked: the page comes, they don't, and the browser offers to allow the first site
        wait(lambda: asked['page'], 'the browser never asked for the page')
        wait(lambda: count_colour(aeros.screenshot('offer'), SUGGEST) > 5000, 'no suggestion came up')
        if asked['first'] or asked['second']:
            aeros.fail('a picture was fetched although its site is not allowed')

        # A: aerOS asks the person itself, and says what for
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('question'), QUESTION) > 0, "aerOS's question did not come up")
        aeros.key('y')
        wait(lambda: asked['first'], 'allowing the site did not fetch its picture')
        wait(lambda: count_colour(aeros.screenshot('first'), RED) >= 120 * 60 // 2, 'the first picture is not showing')
        if asked['second']:
            aeros.fail('the second site was fetched without being allowed')

        # the second site is offered next: this time the answer is no, and nothing is allowed
        wait(lambda: count_colour(aeros.screenshot('offer2'), SUGGEST) > 5000, 'the second suggestion did not come up')
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('question'), QUESTION) > 0, "aerOS's second question did not come up")
        aeros.key('n')
        wait(lambda: count_colour(aeros.screenshot('refused'), SUGGEST) == 0, 'the suggestion stayed after the answer')
        time.sleep(1)
        if asked['second']:
            aeros.fail('the second site was fetched although the answer was no')

        aeros.key('esc')
        aeros.wait_prompt(before, 30, what='browsgui')
        # the firewall has the first rule and not the second
        aeros.cls()
        aeros.run('fw')
        aeros.expect(f'port {first_port}')
        aeros.expect_not(f'port {second_port}')
    finally:
        for server in (first, second, site):
            server.shutdown()


def test_browser_in_a_desktop_window(t):
    """The desktop's Browser window (desktop.aer: the browsgui engine in a window): opened from the
    start menu, an address typed, the page and its picture drawn, a link clicked with the mouse."""
    asked = []
    picture = make_png(120, 60, RED)
    page = (b'<title>In a window</title><h1>Big</h1><p><a href="/two.html">go to page two</a></p>'
            b'<p><img src="/red.png" alt="red"></p>')

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            body, kind = (picture, 'image/png') if self.path == '/red.png' else (
                (TWO, 'text/html') if self.path == '/two.html' else (page, 'text/html'))
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait(condition, what, seconds=60):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what)

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('desktop'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.ask('desktop')
        # aerOS asks before the desktop reads its settings in the home folder: A, always
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) > 0, "aerOS's question did not come up", 90)
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) == 0, 'the question stayed')
        time.sleep(1)

        # Tab opens the start menu, and the Browser is the eighth entry (after the seven other windows)
        aeros.key('tab')
        time.sleep(0.5)
        for _ in range(7):
            aeros.key('down')
        time.sleep(0.5)
        aeros.key('ret')
        time.sleep(1.5)
        # the address bar is ready for typing
        aeros.type(f'http://10.0.2.2:{port}/\n')
        wait(lambda: '/red.png' in asked, 'the Browser never fetched the page and its picture')
        time.sleep(2)
        if count_colour(aeros.screenshot('window'), RED) < 120 * 60 // 2:
            aeros.fail("the page's picture is not drawn in the Browser window")

        # click the link with the mouse (the pointer starts in the middle of the screen): the window is
        # at 150, 58 with its inside 24 down, a 32 high toolbar, then a heading and the link's line
        move = [(-100, -100), (-100, -100), (-112, -4)]
        for dx, dy in move:
            aeros.mouse_move(dx, dy)
            time.sleep(0.1)
        time.sleep(0.5)
        aeros.mouse_buttons(1)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        wait(lambda: '/two.html' in asked, 'clicking the link in the Browser window did not fetch its page', 30)
    finally:
        server.shutdown()


def test_desktop_browser_is_up_to_date(t):
    """The Browser window in the desktop has a copy of browsgui.aer's engine (made by
    tools/make_desktop_browser.py): it must be the copy of the engine as it is now."""
    done = subprocess.run([sys.executable, t.repo('tools', 'make_desktop_browser.py'), '--check'],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr or done.stdout)


FORM_PAGE = (b'<title>Form</title><h1>Search</h1>'
             b'<form action="/search"><input type="text" name="q" size="20"> <input type="submit" value="Go"></form>')
# a tick box (ticked), a list (the second choice picked), a hidden value and a named button
FORM_PAGE_2 = (b'<title>More</title><form action="/go"><input type="checkbox" name="c" checked>'
               b'<select name="s"><option value="a">One<option value="b" selected>Two</select>'
               b'<input type="hidden" name="h" value="x y"><input type="submit" name="go" value="Send"></form>')


def test_browsgui_forms(t):
    """Boxes and buttons on a page: a text box typed in and sent with Enter (the answer on the end of
    the address), then a tick box, a list and a named button, with a hidden value, sent by the button."""
    asked = []

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            if self.path.startswith('/search'):
                body = FORM_PAGE_2
            elif self.path.startswith('/go'):
                body = TWO
            else:
                body = FORM_PAGE
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait(condition, what, seconds=40):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what + f' (asked: {asked})')

    def move_pointer(dx, dy):
        while dx or dy:
            step_x = max(-100, min(100, dx))
            step_y = max(-100, min(100, dy))
            aeros.mouse_move(step_x, step_y)
            dx -= step_x
            dy -= step_y
            time.sleep(0.05)
        time.sleep(0.4)

    def click():
        aeros.mouse_buttons(1)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        time.sleep(0.6)

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('browsgui'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.ask(f'browsgui http://10.0.2.2:{port}/form')
        wait(lambda: asked, 'the browser never asked for the page')
        time.sleep(2)

        # the text box is on the first line under the heading, at about (60, 106) on the screen:
        # click it (it gets a blue edge), type, Enter
        move_pointer(60 - 512, 106 - 384)
        click()
        if count_colour(aeros.screenshot('typing'), LINK) < 100:
            aeros.fail('the text box did not get the blue edge of a box being typed in')
        aeros.type('hello world\n')
        wait(lambda: any(path.startswith('/search') for path in asked), 'Enter did not send the form')
        if '/search?q=hello+world' not in asked:
            aeros.fail(f'the form was sent as something else: {asked}')

        # the next page has a tick box, a list and a button on its first line: untick the box, take
        # the next choice in the list, press the button
        time.sleep(2)
        move_pointer(25 - 60, 58 - 106)
        click()
        move_pointer(35, 0)
        click()
        move_pointer(80, 0)
        click()
        wait(lambda: any(path.startswith('/go') for path in asked), 'the button did not send the form')
        if '/go?s=a&h=x+y&go=Send' not in asked:
            aeros.fail(f'the second form was sent as something else: {asked}')
    finally:
        server.shutdown()


def test_forms_in_the_desktop_browser(t):
    """The Browser window in the desktop: click a text box on a page, type, press Enter."""
    asked = []
    page = b'<title>Form</title><form action="/search"><input type="text" name="q" size="20"><input type="submit" value="Go"></form>'

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(page)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    def wait(condition, what, seconds=60):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what + f' (asked: {asked})')

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('desktop'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.ask('desktop')
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) > 0, "aerOS's question did not come up", 90)
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) == 0, 'the question stayed')
        time.sleep(1)
        aeros.key('tab')
        time.sleep(0.5)
        for _ in range(7):
            aeros.key('down')
        time.sleep(0.5)
        aeros.key('ret')
        time.sleep(1.5)
        aeros.type(f'http://10.0.2.2:{port}/\n')
        wait(lambda: asked, 'the Browser never fetched the page')
        time.sleep(2)

        # the window is at 150, 58: its inside starts 24 down, then a 32 high toolbar, then the box
        # (16 across, 8 down in the page, 24 high)
        for dx, dy in [(-100, -100), (-100, -100), (-112, -48)]:
            aeros.mouse_move(dx, dy)
            time.sleep(0.1)
        time.sleep(0.5)
        aeros.mouse_buttons(1)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        time.sleep(0.6)
        aeros.type('cats\n')
        wait(lambda: '/search?q=cats' in asked, 'typing in the box and Enter did not send the form')
    finally:
        server.shutdown()


MENU_LIT = (200, 220, 255)     # the entry of the picture menu the pointer is on


def test_browsgui_saves_a_picture(t):
    """Right-click a picture: a menu opens at the pointer, Save picture writes the picture to the disk
    (after aerOS asks), named after it, and a click anywhere else closes the menu."""
    asked = []
    picture = make_png(120, 60, RED)
    page = b'<title>Pic</title><p><img src="/red.png" alt="red"></p>'

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            body, kind = (picture, 'image/png') if self.path == '/red.png' else (page, 'text/html')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait(condition, what, seconds=40):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what + f' (asked: {asked})')

    def move_pointer(dx, dy):
        while dx or dy:
            step_x = max(-100, min(100, dx))
            step_y = max(-100, min(100, dy))
            aeros.mouse_move(step_x, step_y)
            dx -= step_x
            dy -= step_y
            time.sleep(0.05)
        time.sleep(0.5)

    def press_button(mask):
        aeros.mouse_buttons(mask)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        time.sleep(0.6)

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
        wait(lambda: '/red.png' in asked, 'the picture was never fetched')
        time.sleep(2)

        # the picture is at 16, 44 (120 x 60): right-click on it, then move onto the first entry
        move_pointer(60 - 512, 70 - 384)
        press_button(2)
        move_pointer(20, 16)
        wait(lambda: count_colour(aeros.screenshot('menu'), MENU_LIT) > 500, 'the right-click did not open the picture menu')

        # a click elsewhere only closes it
        move_pointer(300, 200)
        press_button(1)
        if count_colour(aeros.screenshot('closed'), MENU_LIT) != 0:
            aeros.fail('the menu stayed open after a click somewhere else')

        # open it again, and take Save picture: aerOS asks before the program writes in the folder
        move_pointer(60 + 20 - 380, 70 + 16 - 270)
        move_pointer(-20, -16)
        press_button(2)
        move_pointer(20, 16)
        press_button(1)
        wait(lambda: count_colour(aeros.screenshot('question'), QUESTION) > 0, "aerOS's question did not come up")
        aeros.key('y')
        wait(lambda: count_colour(aeros.screenshot('answered'), QUESTION) == 0, 'the question stayed')
        time.sleep(1)

        aeros.key('esc')
        aeros.wait_prompt(before, 30, what='browsgui')
        aeros.cls()
        aeros.run('la')
        names = [word for word in aeros.text().split() if word.startswith('red') and word.endswith('.png')]
        if len(names) != 1:
            aeros.fail(f'the picture was not saved as red....png: {aeros.text()}')
        aeros.run(f'len(rdb("{names[0]}"))')
        aeros.expect_line(str(len(picture)))
    finally:
        server.shutdown()


def test_save_a_picture_in_the_desktop_browser(t):
    """The same menu in the desktop's Browser window: right-click the picture, Save picture."""
    asked = []
    picture = make_png(120, 60, RED)
    page = b'<title>Pic</title><p><img src="/red.png" alt="red"></p>'

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            body, kind = (picture, 'image/png') if self.path == '/red.png' else (page, 'text/html')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait(condition, what, seconds=60):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what + f' (asked: {asked})')

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('desktop'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        before = aeros.prompts()
        aeros.ask('desktop')
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) > 0, "aerOS's question did not come up", 90)
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('desk'), QUESTION) == 0, 'the question stayed')
        time.sleep(1)
        aeros.key('tab')
        time.sleep(0.5)
        for _ in range(7):
            aeros.key('down')
        time.sleep(0.5)
        aeros.key('ret')
        time.sleep(1.5)
        aeros.type(f'http://10.0.2.2:{port}/\n')
        wait(lambda: '/red.png' in asked, 'the Browser never fetched the picture')
        time.sleep(2)

        # the picture is in the window (at 150, 58) at 166, 122: right-click on it, then the first entry
        for dx, dy in [(-100, -100), (-100, -100), (-112, -44)]:
            aeros.mouse_move(dx, dy)
            time.sleep(0.1)
        time.sleep(0.5)
        aeros.mouse_buttons(2)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        time.sleep(0.6)
        aeros.mouse_move(20, 16)
        time.sleep(0.5)
        wait(lambda: count_colour(aeros.screenshot('menu'), MENU_LIT) > 500, 'the right-click did not open the picture menu')
        aeros.mouse_buttons(1)
        time.sleep(0.2)
        aeros.mouse_buttons(0)
        # aerOS may ask before the desktop writes in the folder (A, from the settings, may cover it already)
        time.sleep(2)
        if count_colour(aeros.screenshot('question'), QUESTION) > 0:
            aeros.key('y')
        time.sleep(2)

        # Esc closes the Browser, then the Clock, then the desktop, and the file is there
        for _ in range(3):
            aeros.key('esc')
            time.sleep(0.7)
        aeros.wait_prompt(before, 30, what='the desktop')
        aeros.cls()
        aeros.run('la')
        names = [word for word in aeros.text().split() if word.startswith('red') and word.endswith('.png')]
        if len(names) != 1:
            aeros.fail(f'the picture was not saved as red....png: {aeros.text()}')
    finally:
        server.shutdown()


def test_browsgui_suggests_where_a_page_moved_to(t):
    """A page that moves to a site the firewall blocks (like example.com to www.example.com): the
    browser suggests allowing the site it moved to, not the one that was typed."""
    asked = {'first': [], 'second': []}
    page = b'<title>Moved</title><h1>You made it</h1>'

    def serve(name, status, headers, body):
        class Pages(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                asked[name].append(self.path)
                self.send_response(status)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                for header, value in headers():
                    self.send_header(header, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        port = free_port()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, port

    second, second_port = serve('second', 200, lambda: [], page)
    first, first_port = serve('first', 302, lambda: [('Location', f'http://10.0.2.2:{second_port}/')], b'')

    def wait(condition, what, seconds=40):
        for _ in range(int(seconds * 5)):
            if condition():
                return
            time.sleep(0.2)
        aeros.fail(what + f' (asked: {asked})')

    try:
        disk = t.disk(files=t.package('browsgui'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {first_port}')          # only the site that was typed
        aeros.ask(f'browsgui http://10.0.2.2:{first_port}/')
        wait(lambda: asked['first'], 'the browser never asked for the page')
        wait(lambda: count_colour(aeros.screenshot('offer'), SUGGEST) > 5000, 'no suggestion came up')
        if asked['second']:
            aeros.fail('the site it moved to was fetched although it is not allowed')
        # A, Y: the rule is for the site it moved to (the second port), and the page loads
        aeros.key('a')
        wait(lambda: count_colour(aeros.screenshot('question'), QUESTION) > 0, "aerOS's question did not come up")
        aeros.key('y')
        wait(lambda: asked['second'], 'allowing the site it moved to did not load the page')
        aeros.key('esc')
    finally:
        first.shutdown()
        second.shutdown()


def test_browsgui_shows_the_page_of_a_403(t):
    """A site that answers 403 Forbidden but sends a page of its own: browsgui shows that page (here
    it has a picture on it, which is then fetched and drawn) instead of only saying it couldn't load."""
    asked = []
    picture = make_png(120, 60, RED)
    page = b'<title>No</title><h1>Access denied</h1><p><img src="/red.png" alt="red"></p>'

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            asked.append(self.path)
            status, body, kind = (200, picture, 'image/png') if self.path == '/red.png' else (403, page, 'text/html')
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    port = free_port()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        disk = t.disk(files=t.package('browsgui'))
        aeros = t.boot(graphics=True, network='user,model=e1000', disk=disk)
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {port}')
        aeros.ask(f'browsgui http://10.0.2.2:{port}/')
        for _ in range(300):
            if '/red.png' in asked:
                break
            time.sleep(0.2)
        else:
            aeros.fail(f"the 403's own page was not shown: its picture was never fetched (asked: {asked})")
        time.sleep(2)
        if count_colour(aeros.screenshot('forbidden'), RED) < 120 * 60 // 2:
            aeros.fail("the picture on the 403's own page is not drawn")
    finally:
        server.shutdown()
