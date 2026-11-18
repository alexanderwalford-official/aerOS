/*
    http.h - fetching a web page (HTTP), over a TCP connection (sys/tcp.h)

    HTTP is what a web browser speaks. To fetch http://example.com/notes.txt, AerOS connects to
    example.com on port 80, sends a request, and reads back the answer until the server closes
    the connection:

        AerOS sends                          the server answers
        -----------                          ------------------
        GET /notes.txt HTTP/1.1              HTTP/1.1 200 OK                <- the status line
        Host: example.com                    Content-Type: text/plain       <- headers, one a
        User-Agent: AerOS/0.01               Content-Length: 12                line, then a
        Accept: (any kind of page)                                             blank line
        Connection: close                    Hello there!                   <- the body: the
        (and a blank line)                                                     page itself

    The status says how it went: 200 is "here it is", 404 "no such page", 301 or 302 "it has
    moved, to the address in the Location header" (AerOS follows those, up to HTTP_REDIRECTS_MAX
    times). The body is sent either all in one, with its length in Content-Length, or in pieces
    ("Transfer-Encoding: chunked"), each with its length in hex first. http_get takes care of both.

    Only plain http://. HTTPS needs TLS (certificates and a whole layer of encryption), which AerOS
    doesn't have yet, so https:// addresses are turned away with HTTP_ERR_HTTPS. That means a page
    can be read, or changed, on the way: fine for something checked afterwards (a package with a
    signature, say), not for anything private.

    Each server needs a web rule first (FW WEB example.com, sys/firewall.h), like everything else
    on the network.
*/
#ifndef HTTP_H
#define HTTP_H

#include <stdint.h>
#include <stddef.h>
#include "firewall.h"

#define HTTP_BODY_MAX      (4 * 1024 * 1024)   // the biggest page AerOS will fetch
#define HTTP_REDIRECTS_MAX 5

// what can go wrong besides the network's own NET_ERR_ codes (sys/net.h)
#define HTTP_ERR_URL        -20   // that isn't an http:// address AerOS understands
#define HTTP_ERR_HTTPS      -21   // an https:// address: AerOS can't do HTTPS yet
#define HTTP_ERR_BAD_ANSWER -22   // the server's answer didn't make sense (or was cut short)
#define HTTP_ERR_TOO_BIG    -23   // more than HTTP_BODY_MAX
#define HTTP_ERR_REDIRECTS  -24   // moved more than HTTP_REDIRECTS_MAX times

typedef struct {
    int status;                 // 200, 404... (0 if no answer came)
    char reason[48];            // the words after it: "OK", "Not Found"
    char content_type[64];      // "text/html", "" if the server didn't say
    char* body;                 // the page (malloc'd, with a '\0' after it): free with http_free
    size_t length;              // its length in bytes (it may hold '\0's of its own)
    char host[FW_NAME_MAX];     // the server it last asked (after any redirects), for messages
    uint16_t port;
} http_response_t;

// Fetch a page (url: "http://example.com/path", or just "example.com/path"). Fills in *out and
// gives back NET_OK if an answer came, whatever its status (check out->status), or an HTTP_ERR_
// or NET_ERR_ code: NET_ERR_BLOCKED means out->host has no web rule. Free the body with http_free
int http_get (const char* url, http_response_t* out);

// give back a response's body
void http_free (http_response_t* response);

// what an HTTP_ERR_ (or NET_ERR_) code means, in words
const char* http_strerror (int error);

#endif /* HTTP_H */
