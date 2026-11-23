#!/usr/bin/env python3
"""
gen_tls_roots.py - make src/sys/tls_roots.c, the certificate authorities AerOS trusts for HTTPS

    python3 tools/gen_tls_roots.py cacert.pem

AerOS trusts a short list of the most widely used "root" certificate authorities (CAs), built
in, rather than the whole of Mozilla's list: a website's certificate is accepted only if it was
signed by one of them, directly or through a chain of others (sys/tls.h). A site whose CA isn't
on the list is refused, so the list is easy to extend: add a name to ROOTS below and run this
again.

The roots' certificates come from Mozilla's list, as curl publishes it (one file, "cacert.pem":
https://curl.se/ca/cacert.pem). Download it yourself, it is not kept in AerOS's source. For each
name in ROOTS this finds that certificate in the file and writes out what BearSSL needs to check
a signature against it: the CA's name, and its public key. Run again with a newer file to
refresh the list (the roots last for 15 years or more, so rarely). Re-running with the same
file gives the same output. (tools/test/tests/test_https.py uses roots_c to make a kernel that
trusts a certificate authority of its own, for its test websites.)

Needs Python's "cryptography" package (pip install cryptography).
"""

import argparse
import datetime
import os
import re
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

# the roots to trust, by the name cacert.pem gives them
ROOTS = [
    # Let's Encrypt
    'ISRG Root X1',
    'ISRG Root X2',
    # Google (and so Cloudflare, and Google's own sites)
    'GTS Root R1',
    'GTS Root R3',
    'GTS Root R4',
    # DigiCert
    'DigiCert Global Root G2',
    'DigiCert Global Root G3',
    'DigiCert TLS RSA4096 Root G5',
    'DigiCert TLS ECC P384 Root G5',
    # GlobalSign
    'GlobalSign Root CA - R3',
    'GlobalSign ECC Root CA - R4',
    # Sectigo (once Comodo, and USERTrust)
    'USERTrust RSA Certification Authority',
    'USERTrust ECC Certification Authority',
    # SSL.com (Cloudflare uses it for many sites, example.com among them)
    'SSL.com TLS RSA Root CA 2022',
    'SSL.com TLS ECC Root CA 2022',
    # Amazon
    'Amazon Root CA 1',
    'Amazon Root CA 2',
    'Amazon Root CA 3',
    'Amazon Root CA 4',
    # Microsoft
    'Microsoft RSA Root Certificate Authority 2017',
    'Microsoft ECC Root Certificate Authority 2017',
]

# BearSSL's numbers for the elliptic curves it knows (bearssl_ec.h)
CURVES = {'secp256r1': 23, 'secp384r1': 24, 'secp521r1': 25}

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'src', 'sys', 'tls_roots.c')

HEADER = """\
/*
    tls_roots.c - the certificate authorities AerOS trusts for HTTPS (see sys/tls.h)

    Made by tools/gen_tls_roots.py from Mozilla's list of roots ({bundle_date}), do not edit
    it by hand: change ROOTS in the script and run it again. Each root is its name (as BearSSL
    wants it, encoded) and its public key. {count} roots:

{names}
*/

#include "../lib/bearssl/inc/bearssl.h"
"""


def read_bundle(path):
    """Every certificate in cacert.pem, as {name: certificate}, and the date the file gives."""
    text = open(path, encoding='utf-8').read()
    date = re.search(r'Certificate data from Mozilla as of: (.*)', text)
    found = {}
    # each one: its name, a line of ====, a "# Fingerprint" comment, then the PEM
    pattern = r'^([^\n]+)\n=+\n(?:#[^\n]*\n)*(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)'
    for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
        found[match.group(1).strip()] = x509.load_pem_x509_certificate(match.group(2).encode())
    return found, date.group(1).strip() if date else 'date unknown'


def c_bytes(data):
    """Bytes as the inside of a C array, 12 to a line."""
    lines = []
    for start in range(0, len(data), 12):
        lines.append('    ' + ', '.join('0x%02X' % b for b in data[start:start + 12]) + ',')
    return '\n'.join(lines)


def trust_anchor(index, name, cert):
    """The C for one root: (its arrays, its table entry)."""
    dn = cert.subject.public_bytes()
    arrays = '/* %s */\nstatic const unsigned char TA%d_DN[] = {\n%s\n};\n' % (name, index, c_bytes(dn))
    key = cert.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        numbers = key.public_numbers()
        modulus = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')
        exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big')
        arrays += 'static const unsigned char TA%d_N[] = {\n%s\n};\n' % (index, c_bytes(modulus))
        arrays += 'static const unsigned char TA%d_E[] = {\n%s\n};\n' % (index, c_bytes(exponent))
        pkey = ('{ BR_KEYTYPE_RSA, { .rsa = { (unsigned char *)TA%d_N, sizeof TA%d_N,\n'
                '            (unsigned char *)TA%d_E, sizeof TA%d_E } } }' % ((index,) * 4))
    elif isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name in CURVES:
        point = key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        arrays += 'static const unsigned char TA%d_Q[] = {\n%s\n};\n' % (index, c_bytes(point))
        pkey = ('{ BR_KEYTYPE_EC, { .ec = { %d, (unsigned char *)TA%d_Q, sizeof TA%d_Q } } }'
                % (CURVES[key.curve.name], index, index))
    else:
        sys.exit('%s: a kind of key AerOS has no code for' % name)
    entry = ('    {\n        { (unsigned char *)TA%d_DN, sizeof TA%d_DN },\n        BR_X509_TA_CA,\n        %s\n    },\n'
             % (index, index, pkey))
    return arrays, entry


def roots_c(roots, bundle_date):
    """The text of tls_roots.c for these roots, a list of (name, certificate)."""
    arrays, entries = [], []
    for index, (name, cert) in enumerate(roots):
        a, e = trust_anchor(index, name, cert)
        arrays.append(a)
        entries.append(e)
    names = '\n'.join('      ' + name for name, cert in roots)
    text = HEADER.format(bundle_date=bundle_date, count=len(roots), names=names)
    text += '\n' + '\n'.join(arrays)
    text += '\nconst br_x509_trust_anchor tls_roots[] = {\n' + ''.join(entries) + '};\n\n'
    text += 'const size_t tls_roots_count = sizeof tls_roots / sizeof tls_roots[0];\n'
    return text


def main():
    parser = argparse.ArgumentParser(description="make src/sys/tls_roots.c from Mozilla's cacert.pem")
    parser.add_argument('bundle', help='cacert.pem (https://curl.se/ca/cacert.pem)')
    parser.add_argument('-o', '--output', default=DEFAULT_OUTPUT, help='the C file to write')
    args = parser.parse_args()

    bundle, bundle_date = read_bundle(args.bundle)
    now = datetime.datetime.now(datetime.timezone.utc)
    roots = []
    for name in ROOTS:
        cert = bundle.get(name)
        if cert is None:
            sys.exit('"%s" is not in %s (Mozilla may have dropped it: take it out of ROOTS)' % (name, args.bundle))
        # (older versions of cryptography only have not_valid_after, which is in UTC but has no zone)
        expires = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        if expires < now:
            sys.exit('"%s" ran out on %s: take it out of ROOTS' % (name, expires.date()))
        roots.append((name, cert))

    with open(args.output, 'w', encoding='utf-8', newline='\n') as out:
        out.write(roots_c(roots, bundle_date))
    print('wrote %d roots to %s' % (len(roots), args.output))


if __name__ == '__main__':
    main()
