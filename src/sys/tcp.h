/*
    tcp.h - TCP: a connection to another computer, carrying a stream of bytes both ways

    UDP (what sealed messages use) sends one packet and hopes. TCP turns packets into a
    connection that delivers every byte, in order, or says it couldn't. It is what the web runs
    on (HTTP, sys/http.h). AerOS opens connections (to a web server, say), but only to a
    computer and port a web rule allows (FW WEB, sys/firewall.h). And it can listen, so other
    computers connect to it (a program serving web pages, say), but only on a port of its own a
    serve rule allows (FW SERVE): see LISTENING below.

    HOW TCP WORKS, IN SHORT

    Each side numbers the bytes it sends ("sequence numbers"), starting from a random number,
    and the other side says how far it has got ("acknowledges" them): "I have everything up to
    byte 5000, send 5001 next". Anything not acknowledged in time is sent again.

        AerOS                                    the web server
          |  SYN, seq 1000           ------->      |   "let's talk, I start at 1000"
          |  <-------   SYN + ACK, seq 7000,       |   "OK, I start at 7000, and I
          |              ack 1001                  |    expect your 1001 next"
          |  ACK, ack 7001           ------->      |   connected: the "handshake"
          |  "GET / HTTP/1.1..."     ------->      |   data, numbered from 1001
          |  <-------   the page, from 7001        |   (as many packets as it takes)
          |  ACK, ack 7001 + what arrived  --->    |
          |  <-------   FIN                        |   "I've finished sending"
          |  FIN + ACK               ------->      |   "so have I": closed

    Every packet also says how much room its sender has for more (the "window"), so neither side
    sends more than the other can take.

    HOW AEROS KEEPS IT SIMPLE
    - Sending waits for each piece to be acknowledged before the next (resending it if not). Slow
      for sending a lot, but AerOS only ever sends small things, like a web request.
    - Receiving takes pieces in order. One that arrives early (a piece before it was lost) is
      dropped: the other side sends it again, and the gap is filled in order.
    - The window AerOS gives is the room left in its buffer (TCP_BUFFER at most), small enough
      that what is on its way always fits in the network card's 32 receive buffers. Packets are
      only collected while AerOS waits for something (sys/net.h), so this matters.
    - No waiting around after closing ("TIME_WAIT"): each connection uses a new random port,
      so a late packet from an old one can't be mistaken for a new one's.

    LISTENING

    The same handshake, the other way round: a program listens on a port (tcp_listen), and
    another computer sends SYN to it. AerOS answers with its own SYN + ACK, and once their ACK
    comes back the connection is open and waits for the program to take it (tcp_accept). From
    then on it is a connection like any other: tcp_receive, tcp_send, tcp_close.

        another computer                          AerOS, listening on 8080
          |  SYN, seq 3000, to 8080  ------->       |   tcp_handle: a serve rule allows it,
          |  <-------   SYN + ACK, seq 9000,        |   so a new connection, "arriving"
          |              ack 3001                   |
          |  ACK, ack 9001           ------->       |   open: waiting to be accepted
          |  "GET /index.html ..."   ------->       |   kept in its buffer until the program
                                                        takes the connection, and reads it

    So that someone can't use up AerOS's connections by starting handshakes they never finish
    (a "SYN flood"), at most TCP_WAITING_MAX connections can wait to be accepted on each port,
    and one whose handshake isn't finished within TCP_ARRIVING_MS is forgotten.

    https://datatracker.ietf.org/doc/html/rfc9293 (TCP) has the whole of it.
*/
#ifndef TCP_H
#define TCP_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define TCP_CONNECTIONS_MAX 8          // connections open at once, both ways
#define TCP_BUFFER          (16 * 1024) // bytes that can arrive before they are read
#define TCP_LISTENERS_MAX   4          // ports listened on at once
#define TCP_WAITING_MAX     4          // connections on one port waiting to be accepted
#define TCP_ARRIVING_MS     10000      // how long a handshake from outside may take to finish

// Open a connection to address:port, which a web rule must allow (FW WEB). Waits for the
// handshake. Gives back the connection's number (0 or more), or a NET_ERR_ code (sys/net.h):
// NET_ERR_BLOCKED (no web rule), NET_ERR_NO_ANSWER (nothing answered), NET_ERR_REFUSED (nothing
// listens on that port there)
int tcp_connect (uint32_t address, uint16_t port);

// send all `length` bytes, waiting until the other side has them. NET_OK, or a NET_ERR_ code
int tcp_send (int connection, const void* data, size_t length);

// Wait up to `ms` milliseconds for bytes to arrive, and copy up to `size` of them into out.
// Gives back how many (more than 0), 0 once the other side has closed the connection and
// everything it sent has been read, NET_ERR_NO_ANSWER if nothing came in time, or
// NET_ERR_CLOSED if the connection was reset
int tcp_receive (int connection, void* out, size_t size, uint32_t ms);

// close a connection (saying so to the other side) and forget it
void tcp_close (int connection);

// Listen on AerOS's port `port`, so other computers can connect to it (see LISTENING). A serve
// rule must allow it (FW SERVE). Gives back the listener's number (0 or more), or a NET_ERR_
// code: NET_ERR_BLOCKED (no serve rule), NET_ERR_IN_USE (something listens there already),
// NET_ERR_SEND (TCP_LISTENERS_MAX listen already), NET_ERR_NO_CARD
int tcp_listen (uint16_t port);

// One look for a connection that has finished its handshake on this listener, without waiting.
// Gives back its number, for tcp_receive / tcp_send / tcp_close, and puts the address it came
// from in *from (may be NULL); or NET_ERR_NO_ANSWER if none has arrived yet. (One look rather
// than a wait: a program waiting for visitors mustn't hold the network lock all that time, so
// it looks again and again, sleeping in between: see tcpaccept in lang/bi_network.c)
int tcp_accept (int listener, uint32_t* from);

// stop listening. Connections already accepted carry on; ones still waiting to be are dropped
void tcp_unlisten (int listener);

// For net.c: a TCP segment (header and data) arrived from `from`. True if it belonged to one of
// AerOS's connections, or started a new one on a port it listens on (and the firewall allows
// it), false to block it
bool tcp_handle (uint32_t from, const uint8_t* segment, size_t length);

#endif /* TCP_H */
