"""HTTPS: GET fetching pages over TLS from websites on this computer (src/sys/tls.c, src/sys/http.c).

AerOS only trusts the certificate authorities built into it (src/sys/tls_roots.c), so these
tests make a certificate authority of their own, and build a copy of the kernel that trusts it
(and nothing else). The websites here then have certificates that are good, expired, for another
name, self-signed, or signed by an authority AerOS doesn't know, and AerOS must load the first
and refuse the rest, saying why.

As in test_web.py, a website here on 127.0.0.1:<port> is 10.0.2.2:<port> to AerOS. BearSSL checks
the name in the certificate against the name asked for, so the good certificate is for the
"name" 10.0.2.2.
"""

import datetime
import http.server
import os
import shutil
import ssl
import subprocess
import sys
import threading

from aeros import GUEST_PROMPT, Skip, free_port

HELLO = b'Hello over TLS!\n'
# many TLS records (16 KB each), and several fills of AerOS's TCP buffer
BIG = b''.join(b'secure line %05d of the big file\n' % i for i in range(8000))
NAME = '10.0.2.2'


def make_key():
    """A new elliptic curve key (fast to make, and to check)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def make_rsa_key():
    """A new RSA key, 2048 bits: what most websites use, so the RSA signature code gets tried."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_certificate(name, key, issuer_name, issuer_key, names=(), authority=False, days=(-30, 365)):
    """A certificate for `key`, signed by issuer_key. names are what it is valid for; days are
    when it starts and ends, from today (negative: in the past)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now + datetime.timedelta(days=days[0]))
               .not_valid_after(now + datetime.timedelta(days=days[1]))
               .add_extension(x509.BasicConstraints(ca=authority, path_length=None), critical=True))
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]),
                                        critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def pem_files(directory, name, certificate, key):
    """Write a certificate and its key as PEM files, and give back their paths."""
    from cryptography.hazmat.primitives import serialization
    cert_path = os.path.join(directory, name + '.crt')
    key_path = os.path.join(directory, name + '.key')
    with open(cert_path, 'wb') as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return cert_path, key_path


def build_kernel(t, authority):
    """A copy of AerOS that trusts `authority` only: the built kernel's files are copied, the
    list of certificate authorities replaced, and AerOS linked again. Gives back its main.elf."""
    tools = t.repo('tools')
    sys.path.insert(0, tools)
    try:
        import gen_tls_roots
    finally:
        sys.path.remove(tools)
    source = t.path('src')
    # (not the disk or the ISOs: only the compiled files are needed, with their dates, so only
    # the one file that changed is compiled again)
    shutil.copytree(t.repo('src'), source, copy_function=shutil.copy2, symlinks=True,
                    ignore=shutil.ignore_patterns('disk.img', '*.iso', 'main.elf'))
    with open(os.path.join(source, 'sys', 'tls_roots.c'), 'w', encoding='utf-8') as f:
        f.write(gen_tls_roots.roots_c([('Test authority', authority)], 'made by the test'))
    done = subprocess.run(['make', 'main.elf'], cwd=source, capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError('building the test kernel failed:\n' + done.stdout[-2000:] + done.stderr[-2000:])
    return os.path.join(source, 'main.elf')


class QuietServer(http.server.ThreadingHTTPServer):
    """A web server that doesn't print what goes wrong in a handshake (some tests make it fail)."""
    def handle_error(self, request, client_address):
        pass


def pages(redirect_to):
    """A handler class for the pages: /hello.txt, /big.txt, and /leave, which moves to redirect_to."""
    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            if self.path == '/leave':
                status, body, headers = 302, b'', [('Location', redirect_to)]
            elif self.path == '/hello.txt':
                status, body, headers = 200, HELLO, []
            elif self.path == '/big.txt':
                status, body, headers = 200, BIG, []
            else:
                status, body, headers = 404, b'No such page\n', []
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            for header, value in headers:
                self.send_header(header, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Pages


def start_server(handler, port, certificate_file=None, key_file=None):
    """A web server on this port: over TLS if it has a certificate."""
    server = QuietServer(('127.0.0.1', port), handler)
    if certificate_file:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_file, key_file)
        # the handshake happens in the thread of each connection, not in the one that accepts them
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def expect_wrapped(aeros, text):
    """The text is on the screen, though a long message wraps onto the next row, and may be cut anywhere."""
    if text not in aeros.text().replace('\n', ''):
        aeros.fail(f'expected to see {text!r}')


def test_https(t):
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise Skip("needs Python's cryptography package: pip install cryptography")
    if shutil.which('make') is None:
        raise Skip("needs make, to build a kernel that trusts the test authority")

    # the authority AerOS is made to trust, another it isn't, and the websites' certificates
    authority_key, other_key = make_key(), make_key()
    authority = make_certificate('Test authority', authority_key, 'Test authority', authority_key, authority=True)
    other = make_certificate('Other authority', other_key, 'Other authority', other_key, authority=True)
    site_key = make_rsa_key()     # (an RSA key: the signature on the key exchange is RSA, the one on the certificate ECDSA)
    ec_key = make_key()
    directory = t.path('certificates')
    os.makedirs(directory)
    sites = {
        'good':       make_certificate(NAME, site_key, 'Test authority', authority_key, [NAME]),
        'good_ec':    make_certificate(NAME, ec_key, 'Test authority', authority_key, [NAME]),
        'expired':    make_certificate(NAME, ec_key, 'Test authority', authority_key, [NAME], days=(-400, -35)),
        'other_name': make_certificate('example.org', ec_key, 'Test authority', authority_key, ['example.org']),
        'self':       make_certificate(NAME, ec_key, NAME, ec_key, [NAME]),
        'unknown':    make_certificate(NAME, ec_key, 'Other authority', other_key, [NAME]),
    }
    keys = {'good': site_key}
    files = {name: pem_files(directory, name, certificate, keys.get(name, ec_key))
             for name, certificate in sites.items()}

    kernel = build_kernel(t, authority)

    # a plain website, and a secure one for each certificate: their ports are chosen first, as each
    # one's /leave page moves to the other kind
    plain_port = free_port()
    secure_ports = {name: free_port() for name in files}
    servers = [start_server(pages(f'https://{NAME}:{secure_ports["good"]}/hello.txt'), plain_port)]
    try:
        for name, (cert, key) in files.items():
            handler = pages(f'http://{NAME}:{plain_port}/hello.txt')
            servers.append(start_server(handler, secure_ports[name], cert, key))

        aeros = t.boot(kernel=kernel, network='user,model=e1000')
        aeros.wait_for(GUEST_PROMPT, 60)
        for port in [plain_port] + list(secure_ports.values()):
            aeros.run(f'fw web {NAME} {port}')
        aeros.cls()

        def get(path_site, path, timeout=120):
            aeros.cls()
            aeros.run(f'get https://{NAME}:{secure_ports[path_site]}{path}', timeout=timeout)

        # a good certificate: a page, and a big one (every byte, in order)
        get('good', '/hello.txt')
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello over TLS!')
        aeros.cls()
        aeros.run(f'get https://{NAME}:{secure_ports["good"]}/big.txt big.txt', timeout=180)
        aeros.expect_line(f'200 OK, {len(BIG)} bytes, text/plain', 'Saved in big.txt.')
        aeros.cls()
        aeros.run('let lines = split(rdf("big.txt"), chr(10))')
        aeros.run('print(len(lines), lines[4000], lines[7999])')
        aeros.expect_line('8001 secure line 04000 of the big file secure line 07999 of the big file')
        # (and the other kind of key)
        get('good_ec', '/hello.txt')
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello over TLS!')

        # from AerLang
        aeros.cls()
        aeros.run(f'httpget("https://{NAME}:{secure_ports["good"]}/hello.txt")', timeout=90)
        aeros.expect('Hello over TLS!')

        # certificates AerOS must refuse, and why
        get('expired', '/hello.txt')
        expect_wrapped(aeros, 'has expired')
        aeros.expect_not('Hello over TLS!')
        get('other_name', '/hello.txt')
        expect_wrapped(aeros, 'is for a different name')
        get('self', '/hello.txt')
        expect_wrapped(aeros, "isn't from an authority AerOS trusts")
        get('unknown', '/hello.txt')
        expect_wrapped(aeros, "isn't from an authority AerOS trusts")
        aeros.expect_not('Hello over TLS!')
        # a refusal lets the next connection through: the good site still works afterwards
        get('good', '/hello.txt')
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello over TLS!')

        # without a web rule for port 443, https can't start, and AerOS says which rule it needs
        aeros.cls()
        aeros.run('get https://example.com/')
        aeros.expect('The firewall has no web rule for example.com port 443', 'FW WEB example.com 443')

        # a plain website that moves to a secure one, and a secure one that moves to a plain one
        aeros.cls()
        aeros.run(f'get http://{NAME}:{plain_port}/leave', timeout=120)
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello over TLS!')
        aeros.cls()
        aeros.run(f'get https://{NAME}:{secure_ports["good"]}/leave', timeout=120)
        aeros.expect_line(f'200 OK, {len(HELLO)} bytes, text/plain', 'Hello over TLS!')
        # (the plain site answers with the same words, so tell them apart: asking for a plain page
        # over https must fail, as a plain site doesn't speak TLS)
        aeros.cls()
        aeros.run(f'get https://{NAME}:{plain_port}/hello.txt', timeout=120)
        aeros.expect_not('Hello over TLS!')
        aeros.expect("Can't fetch")
    finally:
        for server in servers:
            server.shutdown()
