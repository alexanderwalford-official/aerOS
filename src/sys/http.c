/*
    http.c - fetching a web page (see http.h for what HTTP looks like)

    http_get, step by step:

        parse_url        "http://example.com:8080/a/b"  ->  host, port, path
        fetch_once       connect (tcp_connect, and for https:// the encryption on top of it,
                         tls_open), send the request, read everything until the server closes
                         the connection (read_answer), then take the answer apart: the status
                         line, the headers (header_value), and the body, put back together first
                         if it came in chunks (dechunk)
        (a redirect)     fetch_once again, at the address in the Location header (which may
                         change between http:// and https://)

    Reading the whole answer before looking at it, rather than as it arrives, keeps every step
    simple: each works on text that is all there. The request asks for "Connection: close", so
    the server closing the connection is always the end of the answer.
*/

#include "http.h"
#include "net.h"
#include "tcp.h"
#include "tls.h"
#include "boot.h"
#include "trace.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_stdio.h"
#include "../lib/aer_ctype.h"
#include "../lib/primary_definitions.h"

#define PATH_MAX         512
#define REQUEST_MAX      (PATH_MAX + FW_NAME_MAX + 160)
#define ANSWER_MAX       (HTTP_BODY_MAX + 64 * 1024)   // the body, and room for headers and chunk sizes
#define RECEIVE_WAIT_MS  15000                          // how long to wait for the next bytes

// the parts of an address like http://example.com:8080/notes.txt
typedef struct {
    bool secure;                // https:// (encrypted, TLS: sys/tls.h)
    char host[FW_NAME_MAX];     // "example.com"
    uint16_t port;              // 8080 (80 for http://, 443 for https://, if it doesn't say)
    char path[PATH_MAX];        // "/notes.txt" ("/" if it doesn't say)
} url_t;

// ---- small helpers ------------------------------------------------------------------------------

// A connection to a server: plain TCP, or TCP with TLS on top for https:// (tls is NULL if not)
typedef struct {
    int tcp;
    tls_connection_t* tls;
} link_t;

// send all of data, encrypted if the connection is
static int link_send (link_t* link, const void* data, size_t length) {
    return link->tls != NULL ? tls_send(link->tls, data, length) : tcp_send(link->tcp, data, length);
}

// wait up to `ms` for data from the server (decrypted if the connection is encrypted): see
// tcp_receive, which tls_receive answers just like
static int link_receive (link_t* link, void* out, size_t size, uint32_t ms) {
    return link->tls != NULL ? tls_receive(link->tls, out, size, ms) : tcp_receive(link->tcp, out, size, ms);
}

// close the connection (the encryption first, if there is any)
static void link_close (link_t* link) {
    tls_close(link->tls);
    tcp_close(link->tcp);
}

// does text start with prefix, ignoring capitals?
static bool starts_with (const char* text, const char* prefix) {
    return strncasecmp(text, prefix, slen(prefix)) == 0;
}

// ---- the address --------------------------------------------------------------------------------

// Take an address apart: "http://" or "https://" (or nothing: http), then the host, maybe
// ":port", then the path
static int parse_url (const char* text, url_t* url) {
    memset(url, 0, sizeof(url_t));
    url->port = 80;
    if (starts_with(text, "https://")) {
        text += slen("https://");
        url->secure = true;
        url->port = 443;
    } else if (starts_with(text, "http://")) {
        text += slen("http://");
    } else if (strstr(text, "://") != NULL) {
        return HTTP_ERR_URL; // ftp:// or something else AerOS doesn't speak
    }

    // the host: up to a ':' (a port), a '/' (the path) or the end
    size_t length = 0;
    while (text[length] != '\0' && text[length] != ':' && text[length] != '/') {
        length++;
    }
    if (length == 0 || length >= sizeof(url->host)) {
        return HTTP_ERR_URL;
    }
    memcpy(url->host, text, length);
    url->host[length] = '\0';
    text += length;

    // the port, if there is one
    if (*text == ':') {
        text++;
        int port = 0;
        while (isdigit((unsigned char)*text)) {
            port = port * 10 + (*text++ - '0');
            if (port > 65535) {
                return HTTP_ERR_URL;
            }
        }
        if (port == 0) {
            return HTTP_ERR_URL;
        }
        url->port = (uint16_t)port;
    }

    // and the path: everything else, or "/" for the front page
    if (*text == '\0') {
        scpy(url->path, "/", sizeof(url->path));
    } else if (*text == '/' && slen(text) < sizeof(url->path)) {
        scpy(url->path, text, sizeof(url->path));
    } else {
        return HTTP_ERR_URL;
    }
    return NET_OK;
}

// ---- reading the answer -------------------------------------------------------------------------

// Read everything the server sends until it closes the connection, into *answer (malloc'd, with
// a '\0' after it, so text functions can be used on it). NET_OK, or an error
static int read_answer (link_t* link, char** answer, size_t* length) {
    size_t size = 16 * 1024;
    char* buffer = malloc(size + 1);
    size_t have = 0;
    if (buffer == NULL) {
        return HTTP_ERR_TOO_BIG;
    }
    for (;;) {
        // room for another TCP buffer's worth: double the buffer when it runs short, up to
        // ANSWER_MAX (and one TCP buffer more, so an answer of exactly ANSWER_MAX still fits).
        // An answer that fills even that is too big
        if (size - have < TCP_BUFFER) {
            size_t largest = ANSWER_MAX + TCP_BUFFER;
            if (size >= largest) {
                free(buffer);
                return HTTP_ERR_TOO_BIG;
            }
            size = size * 2 < largest ? size * 2 : largest;
            char* bigger = realloc(buffer, size + 1);
            if (bigger == NULL) {
                free(buffer);
                return HTTP_ERR_TOO_BIG;
            }
            buffer = bigger;
        }
        int got = link_receive(link, buffer + have, size - have, RECEIVE_WAIT_MS);
        if (got == 0) {
            break; // the server closed the connection: that's all of it
        }
        if (got < 0) {
            free(buffer);
            return got;
        }
        have += (size_t)got;
        if (have > ANSWER_MAX) {
            free(buffer);
            return HTTP_ERR_TOO_BIG;
        }
    }
    buffer[have] = '\0';
    *answer = buffer;
    *length = have;
    return NET_OK;
}

// The value of a header (name, without the ':') among the headers, into out: "Content-Length:
// 12" gives "12". Header names ignore capitals. False if it isn't there
static bool header_value (const char* headers, const char* name, char* out, size_t size) {
    size_t name_length = slen(name);
    // each header starts a line: look at the start of every line
    for (const char* line = headers; *line != '\0'; ) {
        if (strncasecmp(line, name, name_length) == 0 && line[name_length] == ':') {
            const char* value = line + name_length + 1;
            while (*value == ' ' || *value == '\t') {
                value++;
            }
            size_t length = 0;
            while (value[length] != '\0' && value[length] != '\r' && value[length] != '\n' && length < size - 1) {
                out[length] = value[length];
                length++;
            }
            out[length] = '\0';
            return true;
        }
        const char* next = strchr(line, '\n');
        if (next == NULL) {
            break;
        }
        line = next + 1;
    }
    return false;
}

// Put a chunked body back together, in place. Each piece is its length in hex, a line break,
// that many bytes, and another line break; a piece of length 0 is the end:
//
//     5\r\nHello\r\n7\r\n there!\r\n0\r\n\r\n      ->      Hello there!
//
// Gives back the body's new length, or -1 if it doesn't make sense
static long dechunk (char* body, size_t length) {
    size_t read = 0;
    size_t written = 0;
    for (;;) {
        // the piece's length in hex, up to the end of its line
        size_t piece = 0;
        bool any_digit = false;
        while (read < length && isxdigit((unsigned char)body[read])) {
            char c = (char)tolower((unsigned char)body[read]);
            piece = piece * 16 + (size_t)(isdigit((unsigned char)c) ? c - '0' : c - 'a' + 10);
            any_digit = true;
            read++;
            if (piece > length) {
                return -1;
            }
        }
        // (anything else on the line, like "; something", isn't needed)
        while (read < length && body[read] != '\n') {
            read++;
        }
        read++; // past the '\n'
        if (!any_digit || read > length) {
            return -1;
        }
        if (piece == 0) {
            return (long)written; // the last piece
        }
        if (read + piece > length) {
            return -1;            // cut short
        }
        // the piece moves down to the end of what is put together so far (memmove: they can overlap)
        memmove(body + written, body + read, piece);
        written += piece;
        read += piece;
        // and the line break after it
        if (read < length && body[read] == '\r') {
            read++;
        }
        if (read < length && body[read] == '\n') {
            read++;
        }
    }
}

// ---- one request --------------------------------------------------------------------------------

// Fetch one address (no redirects): fills in *out, and `location` with the Location header if
// there is one ("" if not). NET_OK if an answer came, or an error
static int fetch_once (const url_t* url, http_response_t* out, char* location, size_t location_size) {
    location[0] = '\0';
    scpy(out->host, url->host, sizeof(out->host));
    out->port = url->port;

    // the firewall says where the host is: a web rule made for it (names are looked up then)
    uint32_t address;
    if (!fw_resolve(url->host, url->port, &address)) {
        return NET_ERR_BLOCKED;
    }
    link_t link = { tcp_connect(address, url->port), NULL };
    if (link.tcp < 0) {
        return link.tcp;
    }
    // https: the encryption starts first, and checks who is at the other end (sys/tls.h)
    if (url->secure) {
        int secured = tls_open(link.tcp, url->host, &link.tls);
        if (secured != NET_OK) {
            tcp_close(link.tcp);
            return secured;
        }
    }

    // the request: the path, and the host it is on (one computer can serve many names), with the
    // port when it isn't the usual one (80, or 443 for https)
    char request[REQUEST_MAX];
    char port_part[8] = "";
    if (url->port != (url->secure ? 443 : 80)) {
        sfmt(port_part, sizeof(port_part), ":%u", url->port);
    }
    sfmt(request, sizeof(request),
         "GET %s HTTP/1.1\r\nHost: %s%s\r\nUser-Agent: AerOS/%s\r\nAccept: */*\r\nConnection: close\r\n\r\n",
         url->path, url->host, port_part, AEROS_VERSION);
    TRACE("asking %s for %s", url->host, url->path);
    int result = link_send(&link, request, slen(request));
    char* answer = NULL;
    size_t length = 0;
    if (result == NET_OK) {
        result = read_answer(&link, &answer, &length);
    }
    link_close(&link);
    if (result != NET_OK) {
        return result;
    }

    // The status line: "HTTP/1.1 200 OK". Then the headers, up to a blank line
    char* blank_line = strstr(answer, "\r\n\r\n");
    if (!starts_with(answer, "HTTP/") || blank_line == NULL) {
        free(answer);
        return HTTP_ERR_BAD_ANSWER;
    }
    *blank_line = '\0';                   // so the headers can be searched as text of their own
    char* body = blank_line + 4;
    size_t body_length = length - (size_t)(body - answer);

    const char* status = strchr(answer, ' ');
    out->status = status != NULL ? atoi(status + 1) : 0;
    const char* reason = status != NULL ? strchr(status + 1, ' ') : NULL;
    size_t reason_length = 0;
    if (reason != NULL) {
        reason++;
        while (reason[reason_length] != '\r' && reason[reason_length] != '\0' &&
               reason_length < sizeof(out->reason) - 1) {
            reason_length++;
        }
        memcpy(out->reason, reason, reason_length);
    }
    out->reason[reason_length] = '\0';
    const char* headers = strchr(answer, '\n');
    headers = headers != NULL ? headers + 1 : "";
    header_value(headers, "Content-Type", out->content_type, sizeof(out->content_type));
    header_value(headers, "Location", location, location_size);

    // the body: put back together if it came in pieces, or exactly Content-Length long
    char value[32];
    if (header_value(headers, "Transfer-Encoding", value, sizeof(value)) && strcasecmp(value, "chunked") == 0) {
        long joined = dechunk(body, body_length);
        if (joined < 0) {
            free(answer);
            return HTTP_ERR_BAD_ANSWER;
        }
        body_length = (size_t)joined;
    } else if (header_value(headers, "Content-Length", value, sizeof(value))) {
        size_t promised = (size_t)atoi(value);
        if (promised > body_length) {
            free(answer);
            return HTTP_ERR_BAD_ANSWER; // the connection closed before all of it came
        }
        body_length = promised;
    }

    if (body_length > HTTP_BODY_MAX) {
        free(answer);
        return HTTP_ERR_TOO_BIG;
    }
    // The body is moved to the start of the answer's own buffer, which then shrinks to fit it
    // (realloc), rather than copied into a new one: a 16 MB download then never needs room for
    // two copies of itself at once
    memmove(answer, body, body_length);
    answer[body_length] = '\0';
    char* fitted = realloc(answer, body_length + 1);
    out->body = fitted != NULL ? fitted : answer;
    out->length = body_length;
    TRACE("%s answered %d %s, %u bytes", url->host, out->status, out->reason, (unsigned int)body_length);
    return NET_OK;
}

// ---- what http.h offers -------------------------------------------------------------------------

// Fetch a page, following redirects (http_get takes the network lock and calls this)
static int fetch_following_redirects (const char* url_text, http_response_t* out) {
    memset(out, 0, sizeof(http_response_t));
    url_t url;
    int result = parse_url(url_text, &url);
    for (int redirects = 0; result == NET_OK; redirects++) {
        char location[PATH_MAX + FW_NAME_MAX + 16];
        result = fetch_once(&url, out, location, sizeof(location));
        bool moved = out->status == 301 || out->status == 302 || out->status == 303 ||
                     out->status == 307 || out->status == 308;
        if (result != NET_OK || !moved || location[0] == '\0') {
            break;
        }
        if (redirects == HTTP_REDIRECTS_MAX) {
            result = HTTP_ERR_REDIRECTS;
            break;
        }
        // moved: to a path on the same server ("/new"), or to a whole new address
        TRACE("%s says the page has moved to %s", url.host, location);
        http_free(out);
        if (location[0] == '/') {
            if (slen(location) >= sizeof(url.path)) {
                return HTTP_ERR_URL;
            }
            scpy(url.path, location, sizeof(url.path));
        } else {
            result = parse_url(location, &url);
        }
    }
    return result;
}

// Fetch a page, following redirects (see http.h). The network lock (sys/net.c) is held for the
// whole request, redirects and all, so another thread's request never starts in the middle of
// it. While it waits for the server, the other threads (the prompt among them) still run
int http_get (const char* url_text, http_response_t* out) {
    net_lock();
    int result = fetch_following_redirects(url_text, out);
    net_unlock();
    return result;
}

// give back a response's body (see http.h)
void http_free (http_response_t* response) {
    free(response->body);
    response->body = NULL;
    response->length = 0;
}

// what a code means (see http.h)
const char* http_strerror (int error) {
    switch (error) {
        case HTTP_ERR_URL:        return "that isn't an http:// or https:// address (e.g. https://example.com/page)";
        case HTTP_ERR_BAD_ANSWER: return "the server's answer didn't make sense, or was cut short";
        case HTTP_ERR_TOO_BIG:    return "it is bigger than AerOS will fetch (16 MB)";
        case HTTP_ERR_REDIRECTS:  return "the page moved too many times";
        default:                  return tls_strerror(error);   // (which passes on what it does not know)
    }
}
