/*
    tcp.h - TCP: a connection to another computer, carrying a stream of bytes both ways

    UDP (what sealed messages use) sends one packet and hopes. TCP turns packets into a
    connection that delivers every byte, in order, or says it couldn't. It is what the web runs
    on (HTTP, sys/http.h). AerOS only opens connections (to a web server, say): nothing can
    connect to AerOS, as it never listens. And only to a computer and port a web rule allows
    (FW WEB, sys/firewall.h).

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

    https://datatracker.ietf.org/doc/html/rfc9293 (TCP) has the whole of it.
*/
#ifndef TCP_H
#define TCP_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define TCP_CONNECTIONS_MAX 4          // connections open at once
#define TCP_BUFFER          (16 * 1024) // bytes that can arrive before they are read

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

// For net.c: a TCP segment (header and data) arrived from `from`. True if it belonged to one of
// AerOS's connections (and the firewall still allows it), false to block it
bool tcp_handle (uint32_t from, const uint8_t* segment, size_t length);

#endif /* TCP_H */
