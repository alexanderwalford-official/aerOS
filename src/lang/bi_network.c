/*
    bi_network.c
    AerLang's built-in functions for the network: sending a message to another computer (nsend),
    waiting for one (nrecv), and fetching a web page (httpget). Messages are sealed
    (sys/netseal.c); web pages come over plain TCP (sys/http.h). Both only go to and come
    from computers the firewall allows (sys/firewall.h, the FW command).

    How a built-in works, and how to add one, is at the top of builtins.h.
*/

#include "builtins.h"
#include "../drivers/input/IO.h"
#include "../sys/net.h"
#include "../sys/firewall.h"
#include "../sys/http.h"
#include "../sys/tcp.h"
#include "../sys/sys_clck.h"
#include "../sys/thread.h"

// nsend(host, port, text): send text, sealed, to a computer the firewall allows (FW ALLOW).
// host is an address ("10.0.2.2") or the name a rule was made for. True if it was sent
value_t bi_net_send (int argc, value_t* argv, int line) {
    (void)argc;
    const char* host = str_arg(argv, 0, line, "nsend")->data.chars;
    int32_t port = int_arg(argv, 1, line, "nsend");
    // any value can be sent, it goes as the text print() would show (a new string, released below)
    value_t text = aer_to_string(argv[2]);
    uint32_t address;
    // each step only runs if the one before it passed: a real port number (checked before it is
    // cut down to 16 bits), then the firewall finding the host and allowing it, then the send
    bool sent = port > 0 && port <= 65535 && fw_resolve(host, (uint16_t)port, &address)
                && net_send(address, (uint16_t)port, text.as.obj->data.chars, text.as.obj->length) == NET_OK;
    aer_release(text);
    return aer_bool(sent);
}

// nrecv(port, ms): wait up to ms milliseconds for a sealed message on port, from a computer the
// firewall allows there. The message, or nil if none came. Ctrl+C stops the program (STOP, for
// one in the background)
value_t bi_net_receive (int argc, value_t* argv, int line) {
    (void)argc;
    int32_t port = int_arg(argv, 0, line, "nrecv");
    int32_t ms = int_arg(argv, 1, line, "nrecv");
    if (port < 1 || port > 65535) {
        aer_error(line, "nrecv() needs a port from 1 to 65535");
    }
    // the biggest message there can be, + 1 for the '\0' after it. aer_alloc rather than the
    // stack, as it may be large, and if Ctrl+C abandons the program it is freed with the rest
    char* message = aer_alloc(NET_MESSAGE_MAX + 1);
    // A look every 10 ms, until a message comes or ms has passed. In between, the other threads
    // have the processor (thread_sleep, sys/thread.h), so a program waiting for messages in the
    // background doesn't hold up the prompt. Ctrl+C (STOP in the background) is noticed too
    uint32_t start = clkuptms();
    for (;;) {
        // one look, without waiting, for a message that has already arrived
        int length = net_receive((uint16_t)port, 0, message, NET_MESSAGE_MAX + 1, NULL);
        // a message came: hand it back as a string (a copy, so the buffer can go)
        if (length >= 0) {
            value_t text = aer_string(message, (uint32_t)length);
            aer_free(message);
            return text;
        }
        if (aer_should_stop()) {
            aer_stop(line);
        }
        // the whole of ms has now been waited (for a ms of 0 or less, the one look), and nothing came
        if ((int32_t)(clkuptms() - start) >= ms) {
            aer_free(message);
            return aer_nil();
        }
        thread_sleep(10);
    }
}

// httpget(url): fetch a web page (sys/http.h) from a computer a web rule allows (FW WEB): the
// page's text if it came back with a 2xx status ("OK"), nil if not (no web rule, no answer, no
// such page...). http:// only: AerOS can't do HTTPS yet
value_t bi_http_get (int argc, value_t* argv, int line) {
    (void)argc;
    const char* url = str_arg(argv, 0, line, "httpget")->data.chars;
    http_response_t page;
    int result = http_get(url, &page);
    value_t text = aer_nil();
    if (result == NET_OK && page.status >= 200 && page.status <= 299) {
        text = aer_string(page.body, (uint32_t)page.length);
    }
    http_free(&page);
    return text;
}


// ---- plain TCP: connections made by a program, and to it ----------------------------------------
//
// A program serving web pages: tcplisten(port) once, then over and over tcpaccept to take the next
// visitor, tcprecv and tcpsend to talk to them, and tcpclose. A program talking to a server of
// its own: tcpconnect, then the same. (sys/tcp.h has how TCP works, and LISTENING.)
//
// The numbers handed out are 1 and up, and belong to the program that got them, as dbopen's do:
// each program may only use its own, and the ones it leaves open are closed when it ends
// (network_finish). The waits look once, then sleep, so a program waiting for visitors holds
// the network lock (sys/net.c) only for each look, and the rest of AerOS can use the network
// meanwhile.

static aer_state_t* listener_owners[TCP_LISTENERS_MAX];
static aer_state_t* connection_owners[TCP_CONNECTIONS_MAX];

// The listener a program's number stands for (argument i), from 0. Stops the program with an
// error if it isn't one this program has
static int listener_arg (value_t* argv, int i, int line, const char* fn) {
    int32_t number = int_arg(argv, i, line, fn);
    if (number < 1 || number > TCP_LISTENERS_MAX || listener_owners[number - 1] != aer) {
        aer_error(line, "%s() needs a listener from tcplisten(), and %d isn't one this program has", fn, (int)number);
    }
    return number - 1;
}

// The connection a program's number stands for (argument i), from 0. Stops the program with an
// error if it isn't one this program has
static int connection_arg (value_t* argv, int i, int line, const char* fn) {
    int32_t number = int_arg(argv, i, line, fn);
    if (number < 1 || number > TCP_CONNECTIONS_MAX || connection_owners[number - 1] != aer) {
        aer_error(line, "%s() needs a connection from tcpaccept() or tcpconnect(), and %d isn't one this program has", fn, (int)number);
    }
    return number - 1;
}

// a port number from argument i, 1 to 65535 (anything else stops the program)
static uint16_t port_arg (value_t* argv, int i, int line, const char* fn) {
    int32_t port = int_arg(argv, i, line, fn);
    if (port < 1 || port > 65535) {
        aer_error(line, "%s() needs a port from 1 to 65535", fn);
    }
    return (uint16_t)port;
}

// tcplisten(port): listen on AerOS's own port, so other computers can connect to it. A number
// for tcpaccept, or nil if it can't: no serve rule for the port (FW SERVE <port> allows it), or
// something listens there already
value_t bi_tcp_listen (int argc, value_t* argv, int line) {
    (void)argc;
    int listener = tcp_listen(port_arg(argv, 0, line, "tcplisten"));
    if (listener < 0) {
        return aer_nil();
    }
    listener_owners[listener] = aer;
    return aer_int(listener + 1);
}

// tcpaccept(listener, ms): wait up to ms milliseconds for another computer to connect. A
// number for the connection, or nil if none came. Ctrl+C stops the program (STOP, in the
// background)
value_t bi_tcp_accept (int argc, value_t* argv, int line) {
    (void)argc;
    int listener = listener_arg(argv, 0, line, "tcpaccept");
    int32_t ms = int_arg(argv, 1, line, "tcpaccept");
    // a look every 10 ms, with the other threads running in between (as nrecv does)
    uint32_t start = clkuptms();
    for (;;) {
        int connection = tcp_accept(listener, NULL);
        if (connection >= 0) {
            connection_owners[connection] = aer;
            return aer_int(connection + 1);
        }
        if (aer_should_stop()) {
            aer_stop(line);
        }
        if ((int32_t)(clkuptms() - start) >= ms) {
            return aer_nil();
        }
        thread_sleep(10);
    }
}

// tcpconnect(host, port): open a connection to another computer's port, which a web rule must
// allow (FW WEB <host> <port>). A number for the connection, or nil if it couldn't
value_t bi_tcp_connect (int argc, value_t* argv, int line) {
    (void)argc;
    const char* host = str_arg(argv, 0, line, "tcpconnect")->data.chars;
    uint16_t port = port_arg(argv, 1, line, "tcpconnect");
    uint32_t address;
    if (!fw_resolve(host, port, &address)) {
        return aer_nil();
    }
    int connection = tcp_connect(address, port);
    if (connection < 0) {
        return aer_nil();
    }
    connection_owners[connection] = aer;
    return aer_int(connection + 1);
}

// tcprecv(connection, ms): wait up to ms milliseconds for what the other computer sends. A string
// of what has arrived (up to 16 KB at a time: call it again for more), "" once it has finished
// sending and everything has been read (or the connection was cut), or nil if nothing came in
// time. Ctrl+C stops the program (STOP, in the background)
value_t bi_tcp_receive (int argc, value_t* argv, int line) {
    (void)argc;
    int connection = connection_arg(argv, 0, line, "tcprecv");
    int32_t ms = int_arg(argv, 1, line, "tcprecv");
    // aer_alloc rather than the stack, as it is big, and if the program is stopped meanwhile it
    // is freed with the rest
    char* buffer = aer_alloc(TCP_BUFFER);
    uint32_t start = clkuptms();
    for (;;) {
        int got = tcp_receive(connection, buffer, TCP_BUFFER, 0);
        if (got > 0) {
            value_t text = aer_string(buffer, (uint32_t)got);
            aer_free(buffer);
            return text;
        }
        // 0: they have finished, and everything was read. NET_ERR_CLOSED: it was cut
        if (got == 0 || got == NET_ERR_CLOSED) {
            aer_free(buffer);
            return aer_string("", 0);
        }
        if (aer_should_stop()) {
            aer_stop(line);
        }
        if ((int32_t)(clkuptms() - start) >= ms) {
            aer_free(buffer);
            return aer_nil();
        }
        thread_sleep(10);
    }
}

// tcpsend(connection, data): send a string or bytes, waiting until the other computer has it all.
// True if it got there
value_t bi_tcp_send (int argc, value_t* argv, int line) {
    (void)argc;
    int connection = connection_arg(argv, 0, line, "tcpsend");
    value_t data = argv[1];
    if (data.type != V_STR && data.type != V_BYTES) {
        aer_error(line, "tcpsend() sends a string or bytes, not a %s", aer_type_name(data));
    }
    const void* bytes = data.type == V_STR ? (const void*)data.as.obj->data.chars : (const void*)data.as.obj->data.bytes;
    return aer_bool(tcp_send(connection, bytes, data.as.obj->length) == NET_OK);
}

// tcpclose(connection): finish a connection, telling the other computer
value_t bi_tcp_close (int argc, value_t* argv, int line) {
    (void)argc;
    int connection = connection_arg(argv, 0, line, "tcpclose");
    tcp_close(connection);
    connection_owners[connection] = NULL;
    return aer_nil();
}

// tcpunlisten(listener): stop listening. Connections already accepted carry on
value_t bi_tcp_unlisten (int argc, value_t* argv, int line) {
    (void)argc;
    int listener = listener_arg(argv, 0, line, "tcpunlisten");
    tcp_unlisten(listener);
    listener_owners[listener] = NULL;
    return aer_nil();
}

// close every connection, and stop every listener, that the running program left open
// (aer_builtins_finish calls this when it ends). Only its own: another program may have some too
void network_finish (void) {
    for (int c = 0; c < TCP_CONNECTIONS_MAX; c++) {
        if (connection_owners[c] == aer) {
            tcp_close(c);
            connection_owners[c] = NULL;
        }
    }
    for (int l = 0; l < TCP_LISTENERS_MAX; l++) {
        if (listener_owners[l] == aer) {
            tcp_unlisten(l);
            listener_owners[l] = NULL;
        }
    }
}
