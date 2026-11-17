"""The package server (tools/pkgserver/server.py), the publish command (tools/aerpkg.py) and
AerOS's pkg app (src/disk/bin/pkg.aer) working together.

Everything is signed with a test key made here, never the real one. AerOS's own packagekey() is
whatever key it was built with, so the pkg app is put on the test disk as a copy, pkgt.aer, with
the test key and this computer's server written into it in place of packagekey() and SERVER."""

import base64
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from aeros import Skip, free_port

TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(TOOLS)


class Server:
    """A package server on this computer, with a test key and token of its own."""

    def __init__(self, t):
        try:
            import cryptography  # noqa: F401  (the server and the tools need it)
        except ImportError:
            raise Skip("needs Python's cryptography package: pip install cryptography")
        self.t = t
        self.key = t.path('test.key')
        self.token = t.path('token')
        made = self.tool('aersign.py', 'keygen', self.key)
        self.public = made.stdout.strip().split('\n')[-1]
        made = self.tool('aerpkg.py', 'token', self.token)
        token_hash = made.stdout.strip().split('\n')[-1]

        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        config = t.path('server.conf')
        with open(config, 'w') as f:
            f.write(f'database = {t.path("packages.db")}\n'
                    f'public_key = {self.public}\n'
                    f'token_hash = {token_hash}\n'
                    f'port = {self.port}\n'
                    f'page = {os.path.join(TOOLS, "pkgserver", "index.html")}\n')
        self.log = open(t.path('server.log'), 'w')
        self.proc = subprocess.Popen([sys.executable, os.path.join(TOOLS, 'pkgserver', 'server.py'),
                                      config], stdout=self.log, stderr=subprocess.STDOUT)
        # wait until it is listening
        deadline = time.time() + 20
        while True:
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=1).close()
                break
            except OSError:
                if time.time() > deadline or self.proc.poll() is not None:
                    raise AssertionError('the package server did not start: see server.log')
                time.sleep(0.1)

    def tool(self, script, *args, check=True):
        """Run tools/<script> with these arguments."""
        done = subprocess.run([sys.executable, os.path.join(TOOLS, script), *args],
                              capture_output=True, text=True)
        if check and done.returncode != 0:
            raise AssertionError(f'{script} {" ".join(args)} failed:\n{done.stdout}{done.stderr}')
        return done

    def aerpkg(self, *args, check=True, token=None):
        """Run aerpkg.py publish or unpublish against this server."""
        return self.tool('aerpkg.py', *args, '--server', self.url, '--key', self.key,
                         '--token', token or self.token, check=check)

    def program(self, name, text):
        """Write a package's program in the test's folder, and give its path."""
        path = self.t.path(name + '.aer')
        with open(path, 'w') as f:
            f.write(text)
        return path

    def get(self, path):
        """(status, body) of a GET from the server."""
        try:
            with urllib.request.urlopen(self.url + path, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post(self, upload, token=None):
        """(status, body) of an upload sent straight to /admin/publish, without aerpkg.py."""
        if token is None:
            with open(self.token) as f:
                token = f.read().strip()
        request = urllib.request.Request(self.url + '/admin/publish', method='POST',
                                         data=json.dumps(upload).encode(),
                                         headers={'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        self.log.close()


def signer(key_path):
    """The private key in a test key file, to sign uploads by hand."""
    sys.path.insert(0, TOOLS)
    from aersign import load_private
    return load_private(key_path)


def upload(key, index, programs=None):
    """An upload as aerpkg.py makes it: an index (text), signed with key, and programs."""
    index = index.encode()
    return {'index': base64.b64encode(index).decode(), 'index_sig': key.sign(index).hex(),
            'packages': {n: base64.b64encode(p).decode() for n, p in (programs or {}).items()}}


def must(condition, message):
    if not condition:
        raise AssertionError(message)


def test_package_server(t):
    server = Server(t)
    try:
        # nothing yet, and the landing page
        must(server.get('/index.txt')[0] == 404, 'index.txt should be 404 before any publish')
        status, page = server.get('/')
        must(status == 200 and b'AerOS packages' in page, 'the landing page should be served')

        # publish one: it is served exactly as signed
        greet = b'print("Hello from greet")\n'
        done = server.aerpkg('publish', server.program('greet', greet.decode()), '1.0', 'Says', 'hello')
        must('Published serial 1' in done.stdout, f'first publish: {done.stdout}')
        _, index = server.get('/index.txt')
        _, sig = server.get('/index.sig')
        key = signer(server.key)
        key.public_key().verify(bytes.fromhex(sig.decode()), index)   # raises if it doesn't fit
        must(b'#serial 1\n' in index, 'the index should have #serial 1')
        line = [x for x in index.decode().split('\n') if x.startswith('greet ')][0]
        must(line.endswith(' Says hello') and line.split(' ')[1] == '1.0', f'greet\'s line: {line}')
        key.public_key().verify(bytes.fromhex(line.split(' ')[2]), greet)
        must(server.get('/packages/greet.aer') == (200, greet), 'greet.aer should be served as is')
        old_index, old_sig = index, sig

        # a second package: the first stays, and the serial goes up
        done = server.aerpkg('publish', server.program('notes', 'print("notes")\n'), '0.3', 'Notes')
        must('Published serial 2' in done.stdout, f'second publish: {done.stdout}')
        must(server.get('/packages/greet.aer') == (200, greet), 'greet should still be there')

        # refused: the wrong token, without even reading the upload
        with open(t.path('wrong'), 'w') as f:
            f.write('not-the-token\n')
        done = server.aerpkg('publish', server.program('bad', 'x'), '1', 'Bad', token=t.path('wrong'),
                             check=False)
        must(done.returncode != 0 and 'wrong or missing token' in done.stderr,
             f'a wrong token should be refused: {done.stdout}{done.stderr}')

        # refused: a genuine old index sent again (rolling back), and one with the same serial
        status, said = server.post({'index': base64.b64encode(old_index).decode(),
                                    'index_sig': old_sig.decode(), 'packages': {}})
        must(status == 400 and 'serial is 1' in said, f'an old index should be refused: {said}')

        # refused: a program that isn't the one signed on its line
        evil = b'delf("/home/tester/notes.txt")\n'
        line = f'greet 1.1 {key.sign(greet).hex()} Says hello'
        status, said = server.post(upload(key, f'#serial 9\n{line}\n', {'greet': evil}))
        must(status == 400 and 'doesn\'t match' in said, f'a changed program should be refused: {said}')

        # refused: an index signed by someone else's key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        stranger = Ed25519PrivateKey.generate()
        status, said = server.post(upload(stranger, f'#serial 9\n{line}\n', {'greet': greet}))
        must(status == 400 and 'isn\'t signed' in said, f'a stranger\'s index should be refused: {said}')

        # refused: a program uploaded that the index doesn't list, and an index with no serial
        status, said = server.post(upload(key, f'#serial 9\n{line}\n', {'greet': greet, 'extra': b'x'}))
        must(status == 400 and 'doesn\'t list it' in said, f'an unlisted upload should be refused: {said}')
        status, said = server.post(upload(key, f'{line}\n', {'greet': greet}))
        must(status == 400 and 'no "#serial' in said, f'an index without a serial should be refused: {said}')

        # after all that, nothing changed
        must(server.get('/index.txt')[1].count(b'#serial 2\n') == 1, 'refusals must change nothing')
        must(server.get('/packages/greet.aer') == (200, greet), 'refusals must change nothing')

        # unpublish: the program goes too
        done = server.aerpkg('unpublish', 'notes')
        must('removed notes' in done.stdout, f'unpublish: {done.stdout}')
        must(server.get('/packages/notes.aer')[0] == 404, 'notes.aer should be gone')
        must(b'#serial 3\n' in server.get('/index.txt')[1], 'unpublish should raise the serial')

        # the publish side only answers POSTs; and aerpkg.py never sends the token over plain
        # http:// to anywhere but this computer
        must(server.get('/admin/publish')[0] == 404, 'GET /admin/publish should be 404')
        done = server.tool('aerpkg.py', 'unpublish', 'greet', '--server', 'http://example.com',
                           '--key', server.key, '--token', server.token, check=False)
        must(done.returncode != 0 and 'only go over https' in done.stderr,
             f'plain http to another computer should be refused: {done.stderr}')
    finally:
        server.stop()


class OldSite(http.server.BaseHTTPRequestHandler):
    """A server sending an old, genuinely signed index and program: what someone in the way
    could send AerOS to bring back an old version."""
    files = {}

    def do_GET(self):
        body = self.files.get(self.path)
        self.send_response(200 if body is not None else 404)
        body = body if body is not None else b'No such page\n'
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def pkg_copy(t, name, server_url, public):
    """pkg.aer as /bin/<name>.aer, getting packages from server_url and checking them against
    the test key."""
    with open(os.path.join(REPO, 'src', 'disk', 'bin', 'pkg.aer')) as f:
        source = f.read()
    lines = source.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('let SERVER = '):
            lines[i] = f'let SERVER = "{server_url}"'
    source = '\n'.join(lines).replace('packagekey()', f'"{public}"')
    path = t.path(name + '.aer')
    with open(path, 'w') as f:
        f.write(source)
    return path


def install(aeros, command, timeout=180):
    """Run a pkg install or update, saying yes to its questions and to AerOS's permission
    questions, and wait for it to finish."""
    serial = aeros.serial()
    asked = serial.count('(y/n) ') + serial.count('[ ? ]')
    before = aeros.prompts()
    aeros.ask(command)
    deadline = time.time() + timeout
    while aeros.prompts() <= before:
        if time.time() > deadline:
            aeros.fail(f'{command} did not finish')
        serial = aeros.serial()
        if serial.count('(y/n) ') + serial.count('[ ? ]') > asked:
            asked += 1
            # pkg's own question wants a line; AerOS's permission questions a single key
            aeros.type('y\n' if serial.rstrip().endswith('(y/n)') else 'y')
        time.sleep(0.2)


def test_pkg_from_server(t):
    server = Server(t)
    old = None
    try:
        server.aerpkg('publish', server.program('greet', 'print("Hello from greet 1.0")\n'),
                      '1.0', 'Says', 'hello')
        # the first index and program, kept to send again later
        OldSite.files = {'/index.txt': server.get('/index.txt')[1],
                         '/index.sig': server.get('/index.sig')[1],
                         '/packages/greet.aer': server.get('/packages/greet.aer')[1]}
        old = http.server.ThreadingHTTPServer(('127.0.0.1', free_port()), OldSite)
        threading.Thread(target=old.serve_forever, daemon=True).start()
        old_port = old.server_address[1]

        disk = t.disk(files={
            '/bin/pkgt.aer': pkg_copy(t, 'pkgt', f'http://10.0.2.2:{server.port}', server.public),
            '/bin/pkgold.aer': pkg_copy(t, 'pkgold', f'http://10.0.2.2:{old_port}', server.public)})
        aeros = t.boot(disk=disk, network='user,model=e1000')
        aeros.first_login()
        aeros.run(f'fw web 10.0.2.2 {server.port}')
        aeros.run(f'fw web 10.0.2.2 {old_port}')

        # list and install from the server
        aeros.cls()
        aeros.run('pkgt list', timeout=120)
        aeros.expect_line('greet 1.0: Says hello')
        aeros.cls()
        install(aeros, 'pkgt install greet')
        aeros.expect('Installed greet 1.0: type greet to run it.')
        aeros.cls()
        aeros.run('greet')
        aeros.expect_line('Hello from greet 1.0')
        aeros.run('print(rdf("/apps/serial.txt"))')
        aeros.expect_line('1')

        # a new version, published without rebuilding anything
        server.aerpkg('publish', server.program('greet', 'print("Hello from greet 1.1")\n'),
                      '1.1', 'Says', 'hello')
        aeros.cls()
        aeros.run('pkgt list', timeout=120)
        aeros.expect_line('greet 1.1: Says hello')

        # installing it again is refused: that is pkg update's job
        aeros.cls()
        install(aeros, 'pkgt install greet')
        aeros.expect_line('greet 1.0 is already installed: pkg update greet gets a newer one.')

        # pkg update fetches 1.1, checks it, asks, and replaces 1.0 (and its line)
        aeros.cls()
        install(aeros, 'pkgt update')
        aeros.expect_line('greet: 1.0 -> 1.1')
        aeros.expect('Updated 1, 0 already up to date.')
        aeros.cls()
        aeros.run('greet')
        aeros.expect_line('Hello from greet 1.1')
        aeros.run('print(rdf("/apps/packages.txt"))')
        aeros.expect_line('greet 1.1')

        # nothing newer: nothing to do
        aeros.cls()
        install(aeros, 'pkgt update greet')
        aeros.expect('Updated 0, 1 already up to date.')

        # the old index, genuinely signed, sent again: refused, and 1.1 stays
        aeros.cls()
        install(aeros, 'pkgold update')
        aeros.expect_line('The package index from the server is older than one used before',
                          '(number 1, but 2 was seen), so it won\'t be used.')
        aeros.cls()
        aeros.run('greet')
        aeros.expect_line('Hello from greet 1.1')
    finally:
        if old is not None:
            old.shutdown()
        server.stop()
