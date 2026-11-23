/*
    bi_network.c
    AerLang's built-in functions for the network: sending a message to another computer (nsend),
    waiting for one (nrecv), fetching a web page (httpget), and asking to be allowed to fetch
    from another site (fwallowed, fwallow). Messages are sealed
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
#include "../sys/perm.h"
#include "../sys/syslog.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdio.h"
#include "../lib/aer_ctype.h"

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

// what the last httpget() came to, kept for httpwhy()
static struct {
    int result;                 // NET_OK, or the error (sys/http.h, sys/net.h)
    int status;                 // the page's status, if an answer came
    char reason[48];
    char host[FW_NAME_MAX];     // the site it last asked
    uint16_t port;
} last_get;

// httpget(url): fetch a web page (sys/http.h) from a computer a web rule allows (FW WEB): the
// page's text if it came back with a 2xx status ("OK"), nil if not (no web rule, no answer, no
// such page...). http:// only: AerOS can't do HTTPS yet
value_t bi_http_get (int argc, value_t* argv, int line) {
    (void)argc;
    const char* url = str_arg(argv, 0, line, "httpget")->data.chars;
    http_response_t page;
    int result = http_get(url, &page);
    // what happened, for httpwhy() (the site is the one it last asked: after a redirect, that is
    // where the page moved to, and if the firewall blocked it, the one that needs a rule)
    last_get.result = result;
    last_get.status = page.status;
    scpy(last_get.reason, page.reason, sizeof(last_get.reason));
    scpy(last_get.host, page.host, sizeof(last_get.host));
    last_get.port = page.port;
    value_t text = aer_nil();
    if (result == NET_OK && page.status >= 200 && page.status <= 299) {
        text = aer_string(page.body, (uint32_t)page.length);
    }
    http_free(&page);
    return text;
}


// httpwhy(): why the last httpget() gave nil, as [result, status, words, site, port]: result is 0
// if an answer came (then status is its number, 404 say) or the error's number (-2 is the
// firewall blocking the site: what is blocked is site and port, which after a redirect may not be
// the one asked for, and is what a web rule is needed for), words say it in words, and site and port
// are the last one httpget asked
value_t bi_http_why (int argc, value_t* argv, int line) {
    (void)argc;
    (void)argv;
    (void)line;
    char words[FW_NAME_MAX + 80];
    if (last_get.result == NET_ERR_BLOCKED) {
        sfmt(words, sizeof(words), "the firewall blocks %s port %u", last_get.host, (unsigned int)last_get.port);
    } else if (last_get.result == NET_OK && last_get.status != 0) {
        sfmt(words, sizeof(words), "the site answered %d %s", last_get.status, last_get.reason);
    } else if (last_get.result == NET_OK) {
        scpy(words, "nothing has been fetched yet", sizeof(words));
    } else {
        scpy(words, http_strerror(last_get.result), sizeof(words));
    }
    value_t answer = aer_list(5);
    aer_list_push(answer.as.obj, aer_int(last_get.result));
    aer_list_push(answer.as.obj, aer_int(last_get.status));
    aer_list_push(answer.as.obj, aer_string(words, (uint32_t)slen(words)));
    aer_list_push(answer.as.obj, aer_string(last_get.host, (uint32_t)slen(last_get.host)));
    aer_list_push(answer.as.obj, aer_int(last_get.port));
    return answer;
}


// ---- asking for the firewall to be opened: fwallowed() and fwallow() ----------------------------
//
// The firewall (sys/firewall.h) blocks every site until a person allows it, and a program can't
// type FW (cmd() only works at the prompt). A browser still needs to say "this page's pictures
// come from another site, shall I allow that?", so these two exist: fwallowed asks if a site is
// allowed already, and fwallow asks AerOS to ask the person, and only makes the rule if they say
// yes. The question is AerOS's own (a box over the graphics, or on the text screen, see
// perm_confirm), so a program can't answer it itself, or make it say something else. It only
// ever makes a web rule (fetching pages from one computer on one port), never a sealed one (which
// needs a password) or a serve rule (letting others connect to AerOS)

// The site and port the program gave, checked: a DNS name or an address (letters, digits, dots
// and dashes) and a real port. Stops the program with an error if not
static void site_args (value_t* argv, int line, const char* fn, const char** host, uint16_t* port) {
    *host = str_arg(argv, 0, line, fn)->data.chars;
    int32_t number = int_arg(argv, 1, line, fn);
    size_t length = slen(*host);
    bool good = length > 0 && length < FW_NAME_MAX && number > 0 && number <= 65535;
    for (size_t i = 0; good && i < length; i++) {
        good = isalnum((unsigned char)(*host)[i]) || (*host)[i] == '.' || (*host)[i] == '-';
    }
    if (!good) {
        aer_error(line, "%s() takes a site's name (like example.com) and a port from 1 to 65535", fn);
    }
    *port = (uint16_t)number;
}

// Is there a web rule for this site and port? (An address like 10.0.2.2 is looked for by its
// address, a name by the name the rule was made for: a name is never looked up here)
static bool web_allowed (const char* host, uint16_t port) {
    uint32_t address;
    const fw_rule_t* rule;
    if (net_parse_address(host, &address)) {
        rule = fw_find(address, port);
    } else {
        rule = fw_find_name(host, port);
    }
    return rule != NULL && rule->web;
}

// fwallowed(host, port): true if fetching web pages from this site on this port is allowed
// already (FW WEB), so httpget would be let through. Changes nothing, asks nothing
value_t bi_fw_allowed (int argc, value_t* argv, int line) {
    (void)argc;
    const char* host;
    uint16_t port;
    site_args(argv, line, "fwallowed", &host, &port);
    return aer_bool(web_allowed(host, port));
}

// fwallow(host, port): ask the person to allow fetching web pages from this site on this port,
// like FW WEB host port. True if it is allowed now (it was already, or they said yes), false if
// they said no, or the name couldn't be found. Asked every time, never "always", and never
// without a person to ask (a program in the background gets a no). A name is looked up with DNS
// once the person has said yes, as FW WEB does, which can't be sealed (sys/net.h)
value_t bi_fw_allow (int argc, value_t* argv, int line) {
    (void)argc;
    const char* host;
    uint16_t port;
    site_args(argv, line, "fwallow", &host, &port);
    if (web_allowed(host, port)) {
        return aer_bool(true);
    }
    // who is asking: the program's name, or the prompt
    const char* program = perm_program_name();
    char question[FW_NAME_MAX + 160];
    sfmt(question, sizeof(question), "%s wants to allow web pages from %s, port %u (FW WEB)",
         program[0] != '\0' ? program : "The prompt", host, (unsigned int)port);
    if (!perm_confirm(question)) {
        return aer_bool(false);
    }
    uint32_t address;
    char name[FW_NAME_MAX] = "";
    if (!net_parse_address(host, &address)) {
        if (net_lookup(host, &address) != NET_OK) {
            return aer_bool(false);
        }
        scpy(name, host, sizeof(name));
    }
    bool made = fw_allow_web(address, port, name);
    syslog_add("firewall", "web", "%s allowed web pages from %s port %u (agreed to by the person at the keyboard)",
               program[0] != '\0' ? program : "the prompt", host, (unsigned int)port);
    return aer_bool(made);
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
