/*
    tls.h - HTTPS: a TCP connection (sys/tcp.h) that is encrypted, and proves who it is talking to

    https:// is HTTP (sys/http.h) spoken through TLS, which sits between HTTP and TCP:

        HTTP        "GET / HTTP/1.1..."          what is said
        TLS         encrypted, and checked       tls_send / tls_receive (this file)
        TCP         bytes, in order              tcp_send / tcp_receive

    Making the connection (the "handshake") takes a few messages:

        AerOS                                         the web server
          |  hello: "I speak TLS 1.2, with these      |
          |  ciphers, and I want example.com"  -->    |
          |  <-- hello, its certificate, and a        |   the certificate is the server's
          |      signed share of a key exchange       |   public key, signed by a "certificate
          |  AerOS's share of the key exchange  -->   |   authority" (CA)
          |  <-- both now work out the same secret    |
          |      key, and everything after it is      |
          |      encrypted with it (AES-GCM or        |
          |      ChaCha20-Poly1305), both ways        |

    What AerOS checks before it trusts the server (BearSSL does it, lib/bearssl):
    - the certificate was signed by a CA AerOS knows, directly or through a chain of them. The
      CAs AerOS knows are a short list built in (sys/tls_roots.c, made by tools/gen_tls_roots.py)
    - none of the certificates in the chain has run out, or isn't valid yet: so AerOS's clock
      (sys/sys_clck.h) has to be about right
    - the certificate is for the name AerOS asked for ("example.com"), not for some other site
    - the server proves it has the certificate's private key, by signing the key exchange

    What it doesn't do: TLS 1.2 only (no 1.3, and no older ones, which are no longer safe), no
    certificate revocation lists, no client certificates, no session resuming, and a server
    must offer ECDHE (an elliptic curve key exchange) with AES-GCM or ChaCha20-Poly1305, which
    every maintained server does. A server that closes the connection without saying so (no
    "close notify") is taken to have finished, as the end of a page that has no length.

    Like all network use, it needs a web rule for the server, with port 443 for https (FW WEB
    example.com 443, sys/firewall.h).

    BearSSL (https://bearssl.org, lib/bearssl) does the TLS itself. This file connects it to
    AerOS: the TCP connection it reads and writes, the random numbers (lib/crypto.h) and the date
    (sys/sys_clck.h) it needs, and the trusted CAs.
*/
#ifndef TLS_H
#define TLS_H

#include <stdint.h>
#include <stddef.h>

// what can go wrong besides the network's own NET_ERR_ codes (sys/net.h)
#define TLS_ERR_EXPIRED   -30   // the server's certificate has run out (or isn't valid yet)
#define TLS_ERR_NAME      -31   // the certificate is for a different name than the one asked for
#define TLS_ERR_UNTRUSTED -32   // the certificate wasn't signed by a CA AerOS trusts (self-signed, say)
#define TLS_ERR_CERT      -33   // some other problem with the certificate or its signature
#define TLS_ERR_HANDSHAKE -34   // the server and AerOS couldn't agree on a TLS 1.2 connection
#define TLS_ERR_BROKEN    -35   // the connection failed after it was made (damaged or cut data)

typedef struct tls_connection tls_connection_t;

// Make an encrypted connection to `host` over a TCP connection (tcp_connect) that is already open:
// the handshake, and the checks above. Gives back NET_OK and the connection in *out, or an error
// (TLS_ERR_, or the network's own: the TCP connection is left open, close it with tcp_close)
int tls_open (int tcp_connection, const char* host, tls_connection_t** out);

// send all `length` bytes, encrypted. NET_OK, or an error
int tls_send (tls_connection_t* connection, const void* data, size_t length);

// like tcp_receive (sys/tcp.h), but decrypted: waits up to `ms` milliseconds for each piece of
// the answer from the network, and gives back how many bytes (more than 0), 0 once the server
// has finished, NET_ERR_NO_ANSWER if nothing came in time, or another error
int tls_receive (tls_connection_t* connection, void* out, size_t size, uint32_t ms);

// say goodbye to the server and forget the connection (the TCP connection is not closed)
void tls_close (tls_connection_t* connection);

// what an error means, in words (a TLS_ERR_ code, or the network's)
const char* tls_strerror (int error);

#endif /* TLS_H */
