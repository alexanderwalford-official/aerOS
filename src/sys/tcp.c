/*
    tcp.c - TCP connections (see tcp.h for how TCP works, and how AerOS keeps it simple)

    The pieces, in the order a connection uses them:

        tcp_connect      sends SYN and waits for SYN + ACK          (send_and_wait)
        tcp_send         sends each piece and waits for its ACK     (send_and_wait)
        tcp_receive      waits for bytes to be in the buffer        (net_poll, then the buffer)
        tcp_close        sends FIN, waits a little for its ACK, and forgets the connection

    and for connections from outside (LISTENING in tcp.h):

        tcp_listen       notes the port in listeners[], if a serve rule allows it
        (tcp_handle)     a SYN for that port: a new connection, ARRIVING, and SYN + ACK back
                         (arrive); their ACK makes it OPEN, waiting to be accepted
        tcp_accept       hands over one that is open and waiting
        tcp_unlisten     forgets the port, and the connections still waiting on it

    and underneath them all, tcp_handle: net.c calls it for each TCP segment that arrives, and it
    updates the connection the segment belongs to (what is acknowledged, new bytes into the
    buffer, the other side closing). Nothing happens in the background: segments are only taken
    in while one of the functions above is waiting, through net_poll.

    A TCP segment's header, 20 bytes, then maybe options (here, only MSS), then the data:

        0   source port (2)               12  data offset: header length in 32 bit words (top
        2   destination port (2)              4 bits of the byte)
        4   sequence number (4)           13  flags: FIN 1, SYN 2, RST 4, PSH 8, ACK 16
        8   acknowledgement number (4)    14  window: room for more (2)
                                          16  checksum (2)      18  urgent pointer (2), unused
*/

#include "tcp.h"
#include "net.h"
#include "firewall.h"
#include "sys_clck.h"
#include "trace.h"
#include "../lib/crypto.h"
#include "../lib/aer_stdlib.h"
#include "../lib/primary_definitions.h"

#define TCP_HEADER     20
#define MSS_OPTION     4        // kind 2, length 4, then the size (2 bytes)
#define OUR_MSS        1460     // the most data in one segment: a 1500 byte packet less 40 of headers

#define FLAG_FIN 0x01   // "I have finished sending"
#define FLAG_SYN 0x02   // "let's start": the first segment each way
#define FLAG_RST 0x04   // "no such connection": stop at once
#define FLAG_PSH 0x08   // "pass this on now" (always set on data: AerOS never holds data back)
#define FLAG_ACK 0x10   // the acknowledgement number means something

#define FIRST_WAIT_MS 1000      // how long before a segment is sent again (doubling each time)
#define TRIES         5         // ...and how many times: 1 + 2 + 4 + 8 + 16 seconds at most

typedef enum {
    CONNECTING,     // SYN sent, waiting for SYN + ACK
    ARRIVING,       // from outside: their SYN came, our SYN + ACK went, waiting for their ACK
    OPEN,           // connected: bytes can go both ways
    THEY_CLOSED,    // their FIN arrived: they send no more, but we still may
} state_t;

typedef struct {
    bool used;
    state_t state;
    uint32_t address;         // the other computer
    uint16_t port;            // ...its port
    uint16_t our_port;        // and ours: picked at random, or the one listened on

    int listener;             // the listener it arrived on, or -1 for one AerOS opened
    bool waiting;             // arrived on a listener, and not accepted yet
    uint32_t arrived;         // clkuptms() when its SYN came (for TCP_ARRIVING_MS)

    uint32_t send_next;       // the number our next new byte gets
    uint32_t send_unacked;    // our oldest byte they haven't acknowledged yet
    uint16_t their_window;    // how much more they have room for
    uint16_t their_mss;       // the most they want in one segment

    uint32_t receive_next;    // the number of the next byte we expect from them
    uint8_t* buffer;          // what has arrived, waiting to be read (TCP_BUFFER bytes)
    size_t buffered;

    bool reset;               // they sent RST: the connection is gone
} connection_t;

static connection_t connections[TCP_CONNECTIONS_MAX];

// the ports programs listen on (tcp_listen)
static struct {
    bool used;
    uint16_t port;
} listeners[TCP_LISTENERS_MAX];

// ---- small helpers ------------------------------------------------------------------------------

// numbers in network order (big endian: highest byte first), as in net.c
static void put16 (uint8_t* p, uint16_t value) {
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
}

// put a 32 bit number in network order
static void put32 (uint8_t* p, uint32_t value) {
    put16(p, (uint16_t)(value >> 16));
    put16(p + 2, (uint16_t)value);
}

// read a 16 bit number in network order
static uint16_t get16 (const uint8_t* p) {
    return (uint16_t)((p[0] << 8) | p[1]);
}

// read a 32 bit number in network order
static uint32_t get32 (const uint8_t* p) {
    return ((uint32_t)get16(p) << 16) | get16(p + 2);
}

// Add up bytes as 16 bit numbers onto `sum`, for the checksum (an odd last byte counts as if a
// 0 followed it)
static uint32_t add_up (uint32_t sum, const uint8_t* bytes, size_t length) {
    for (size_t i = 0; i + 1 < length; i += 2) {
        sum += get16(bytes + i);
    }
    if (length % 2 == 1) {
        sum += (uint32_t)bytes[length - 1] << 8;
    }
    return sum;
}

// The TCP checksum: like the IP header's (net.c's ip_checksum), but over a "pseudo header" of the
// two addresses, the protocol and the length first, then the whole segment. Over a segment that
// already holds its checksum, this gives 0 when nothing was changed on the way
static uint16_t tcp_checksum (uint32_t from, uint32_t to, const uint8_t* segment, size_t length) {
    uint8_t pseudo[12];
    put32(pseudo, from);
    put32(pseudo + 4, to);
    pseudo[8] = 0;
    pseudo[9] = 6;                       // TCP
    put16(pseudo + 10, (uint16_t)length);
    uint32_t sum = add_up(add_up(0, pseudo, sizeof(pseudo)), segment, length);
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }
    return (uint16_t)~sum;
}

// is `a` after `b`? Sequence numbers wrap round past 0xFFFFFFFF, so this looks at the difference
// as a signed number: anything up to 2 GB ahead counts as after
static bool after (uint32_t a, uint32_t b) {
    return (int32_t)(a - b) > 0;
}

// the room left in a connection's buffer: what AerOS tells the other side it can send
static uint16_t room (const connection_t* c) {
    return (uint16_t)(TCP_BUFFER - c->buffered);
}

// ---- sending ------------------------------------------------------------------------------------

// one segment to send: its flags, its first sequence number, and its data (if any)
typedef struct {
    uint8_t flags;
    uint32_t sequence;
    const uint8_t* data;
    size_t length;
} outgoing_t;

// build a segment and send it. A SYN carries the MSS option, saying how much fits in one segment
static int send_segment (const connection_t* c, const outgoing_t* out) {
    static uint8_t segment[TCP_HEADER + MSS_OPTION + OUR_MSS];
    bool syn = (out->flags & FLAG_SYN) != 0;
    size_t header = TCP_HEADER + (syn ? MSS_OPTION : 0);
    if (header + out->length > sizeof(segment)) {
        return NET_ERR_TOO_BIG;
    }
    memset(segment, 0, header);
    put16(segment, c->our_port);
    put16(segment + 2, c->port);
    put32(segment + 4, out->sequence);
    put32(segment + 8, (out->flags & FLAG_ACK) ? c->receive_next : 0);
    segment[12] = (uint8_t)((header / 4) << 4);   // the header's length, in 32 bit words
    segment[13] = out->flags;
    put16(segment + 14, room(c));
    if (syn) {
        segment[20] = 2;                          // option kind 2: MSS
        segment[21] = 4;                          // ...4 bytes long
        put16(segment + 22, OUR_MSS);
    }
    memcpy(segment + header, out->data, out->length);
    // the checksum last, over the finished segment with its own place still 0
    put16(segment + 16, tcp_checksum(net_own_address(), c->address, segment, header + out->length));
    return net_send_tcp(c->address, segment, header + out->length);
}

// send just an acknowledgement: "I have everything up to receive_next, and this much room"
static void send_ack (const connection_t* c) {
    outgoing_t ack = { FLAG_ACK, c->send_next, NULL, 0 };
    send_segment(c, &ack);
}

// Send a segment, and wait for `done` to become true (tcp_handle changes the connection as
// answers arrive), sending it again each time a wait runs out, for TRIES tries. NET_OK when done,
// NET_ERR_NO_ANSWER when the tries run out, NET_ERR_REFUSED or NET_ERR_CLOSED on a reset
static int send_and_wait (connection_t* c, const outgoing_t* out, bool (*done)(const connection_t*)) {
    uint32_t wait_ms = FIRST_WAIT_MS;
    for (int attempt = 0; attempt < TRIES; attempt++) {
        if (attempt > 0) {
            TRACE("no answer from port %u in %u ms: sending again", c->port, (unsigned int)(wait_ms / 2));
        }
        int result = send_segment(c, out);
        if (result != NET_OK) {
            return result;
        }
        uint32_t start = clkuptms();
        while (clkuptms() - start < wait_ms) {
            net_poll();
            if (c->reset) {
                return c->state == CONNECTING ? NET_ERR_REFUSED : NET_ERR_CLOSED;
            }
            if (done(c)) {
                return NET_OK;
            }
            net_pause();
        }
        wait_ms *= 2;
    }
    return NET_ERR_NO_ANSWER;
}

// for send_and_wait: has the handshake finished?
static bool connected (const connection_t* c) {
    return c->state != CONNECTING;
}

// for send_and_wait: has everything sent been acknowledged?
static bool all_acknowledged (const connection_t* c) {
    return c->send_unacked == c->send_next;
}

// ---- what arrives -------------------------------------------------------------------------------

// the MSS option in a SYN's header, if it has one (otherwise TCP's default, 536)
static uint16_t read_mss (const uint8_t* segment, size_t header) {
    size_t at = TCP_HEADER;
    while (at < header) {
        uint8_t kind = segment[at];
        if (kind == 0) {
            break;                       // the end of the options
        }
        if (kind == 1) {
            at++;                        // "no operation": one byte of padding
            continue;
        }
        if (at + 1 >= header || segment[at + 1] < 2) {
            break;                       // a broken option: stop reading them
        }
        if (kind == 2 && segment[at + 1] == 4 && at + 3 < header) {
            return get16(segment + at + 2);
        }
        at += segment[at + 1];
    }
    return 536;
}

// give back a connection's buffer and empty its place, for the next connection
static void forget (connection_t* c) {
    free(c->buffer);
    memset(c, 0, sizeof(connection_t));
}

// the listener on AerOS's port `port`, or -1 if nothing listens there
static int listener_on (uint16_t port) {
    for (int l = 0; l < TCP_LISTENERS_MAX; l++) {
        if (listeners[l].used && listeners[l].port == port) {
            return l;
        }
    }
    return -1;
}

// A SYN from outside, for AerOS's port `to_port` (see LISTENING in tcp.h): if a program listens
// there and a serve rule lets `from` in, a new connection, ARRIVING, and our SYN + ACK back.
// True if it was taken
static bool arrive (uint32_t from, uint16_t from_port, uint16_t to_port, uint32_t sequence, const uint8_t* segment, size_t header, uint16_t window) {
    int l = listener_on(to_port);
    if (l < 0 || !fw_serves(from, to_port)) {
        TRACE("someone tried to connect to port %u: %s", to_port,
              l < 0 ? "nothing listens there" : "no serve rule lets them in (FW SERVE)");
        return false;
    }
    // Forget handshakes on this port that never finished, then count the ones still waiting to
    // be accepted. Past TCP_WAITING_MAX this SYN is ignored: they send it again later
    uint32_t now = clkuptms();
    int waiting = 0;
    int free_place = -1;
    for (int i = 0; i < TCP_CONNECTIONS_MAX; i++) {
        connection_t* c = &connections[i];
        if (c->used && c->listener == l && c->state == ARRIVING && now - c->arrived >= TCP_ARRIVING_MS) {
            TRACE("forgot a connection from port %u that never finished its handshake", c->port);
            forget(c);
        }
        if (c->used && c->listener == l && c->waiting) {
            waiting++;
        }
        if (!c->used && free_place < 0) {
            free_place = i;
        }
    }
    if (waiting >= TCP_WAITING_MAX || free_place < 0) {
        TRACE("too many connections waiting on port %u: this one has to try again", to_port);
        return false;
    }
    connection_t* c = &connections[free_place];
    memset(c, 0, sizeof(connection_t));
    c->buffer = malloc(TCP_BUFFER);
    if (c->buffer == NULL) {
        return false;
    }
    // our first sequence number is random, as for a connection AerOS opens (see open_connection)
    uint32_t first;
    crypto_random(&first, sizeof(first));
    c->used = true;
    c->state = ARRIVING;
    c->address = from;
    c->port = from_port;
    c->our_port = to_port;
    c->listener = l;
    c->waiting = true;
    c->arrived = now;
    c->receive_next = sequence + 1;         // their SYN counts as one byte
    c->their_window = window;
    c->their_mss = read_mss(segment, header);
    c->send_unacked = first;
    c->send_next = first + 1;               // ...and so does ours
    TRACE("port %u is connecting to our port %u: sending SYN + ACK", from_port, to_port);
    outgoing_t syn_ack = { FLAG_SYN | FLAG_ACK, first, NULL, 0 };
    send_segment(c, &syn_ack);
    return true;
}

// a segment arrived (see tcp.h): which connection is it for, and what does it change?
bool tcp_handle (uint32_t from, const uint8_t* segment, size_t length) {
    if (length < TCP_HEADER || tcp_checksum(from, net_own_address(), segment, length) != 0) {
        return false; // too short, or changed on the way
    }
    uint16_t from_port = get16(segment);
    uint16_t to_port = get16(segment + 2);
    uint32_t sequence = get32(segment + 4);
    uint32_t acknowledged = get32(segment + 8);
    size_t header = (size_t)(segment[12] >> 4) * 4;
    uint8_t flags = segment[13];
    uint16_t window = get16(segment + 14);
    if (header < TCP_HEADER || header > length) {
        return false;
    }

    // It must be for one of our connections...
    connection_t* c = NULL;
    for (int i = 0; i < TCP_CONNECTIONS_MAX; i++) {
        connection_t* candidate = &connections[i];
        if (candidate->used && candidate->address == from && candidate->port == from_port &&
            candidate->our_port == to_port) {
            c = candidate;
        }
    }
    // ...or a new one, to a port a program listens on: a SYN, on its own (not SYN + ACK, which
    // is only ever an answer). Anything else is blocked
    if (c == NULL) {
        bool syn_only = (flags & FLAG_SYN) && !(flags & (FLAG_ACK | FLAG_RST));
        return syn_only && arrive(from, from_port, to_port, sequence, segment, header, window);
    }
    // and the firewall must still allow it: a web rule for one AerOS opened, a serve rule for
    // one from outside (FW DEL takes effect at once, on connections already open too)
    if (c->listener < 0) {
        const fw_rule_t* rule = fw_find(from, from_port);
        if (rule == NULL || !rule->web) {
            return false;
        }
    } else if (!fw_serves(from, to_port)) {
        return false;
    }

    // RST: the connection is over (refused, if it was still connecting)
    if (flags & FLAG_RST) {
        TRACE("port %u reset the connection", from_port);
        c->reset = true;
        return true;
    }

    // from outside, still in the handshake: their SYN again means our SYN + ACK was lost, so it
    // goes again; their ACK of it opens the connection, and may carry the first data too
    if (c->state == ARRIVING) {
        if (flags & FLAG_SYN) {
            outgoing_t syn_ack = { FLAG_SYN | FLAG_ACK, c->send_unacked, NULL, 0 };
            send_segment(c, &syn_ack);
            return true;
        }
        if (!(flags & FLAG_ACK) || acknowledged != c->send_next) {
            return true;
        }
        c->send_unacked = acknowledged;
        c->state = OPEN;
        TRACE("port %u has connected to our port %u: waiting to be accepted", from_port, to_port);
    }

    // the answer to our SYN: they start numbering their bytes at `sequence`
    if (c->state == CONNECTING) {
        if ((flags & FLAG_SYN) && (flags & FLAG_ACK) && acknowledged == c->send_next) {
            c->receive_next = sequence + 1;
            c->send_unacked = acknowledged;
            c->their_window = window;
            c->their_mss = read_mss(segment, header);
            c->state = OPEN;
            TRACE("connected to port %u: they acknowledged our SYN, and sent their own", from_port);
            send_ack(c);
        }
        return true;
    }
    // their SYN + ACK again: our ACK was lost, so say it again
    if (flags & FLAG_SYN) {
        send_ack(c);
        return true;
    }

    // what they acknowledge: everything of ours before `acknowledged`
    if ((flags & FLAG_ACK) && after(acknowledged, c->send_unacked) && !after(acknowledged, c->send_next)) {
        c->send_unacked = acknowledged;
    }
    if (flags & FLAG_ACK) {
        c->their_window = window;
    }

    // their data: taken only if it is the next we expect, and only as much as there is room for
    size_t data_length = length - header;
    if (data_length > 0 && sequence == c->receive_next) {
        size_t taken = data_length < room(c) ? data_length : room(c);
        memcpy(c->buffer + c->buffered, segment + header, taken);
        c->buffered += taken;
        c->receive_next += (uint32_t)taken;
    }
    // their FIN, once every byte before it has arrived: they have finished sending
    bool fin_in_order = (flags & FLAG_FIN) && sequence + (uint32_t)data_length == c->receive_next;
    if (fin_in_order && c->state == OPEN) {
        c->receive_next++;                   // a FIN counts as one byte
        c->state = THEY_CLOSED;
        TRACE("port %u has finished sending", from_port);
    }
    // anything with data or a FIN gets an acknowledgement, even one that came early or twice
    // (then it repeats what we have, so they know what to send again)
    if (data_length > 0 || (flags & FLAG_FIN)) {
        send_ack(c);
    }
    return true;
}

// ---- what tcp.h offers --------------------------------------------------------------------------

// Each function here takes the network lock (sys/net.c) and lets go of it at the end, around
// the work itself, which is in the static function just above it. The waits inside (net_pause)
// let other threads run, and the lock keeps them out of the network until this is done

// Open a connection, sending SYN and waiting for the answer (tcp_connect takes the network lock
// and calls this)
static int open_connection (uint32_t address, uint16_t port) {
    if (!net_up()) {
        return NET_ERR_NO_CARD;
    }
    // only to a computer and port a web rule allows: checked before anything at all is sent
    const fw_rule_t* rule = fw_find(address, port);
    if (rule == NULL || !rule->web) {
        TRACE("not connecting to port %u: no web rule allows it (FW WEB)", port);
        return NET_ERR_BLOCKED;
    }
    int number = 0;
    while (number < TCP_CONNECTIONS_MAX && connections[number].used) {
        number++;
    }
    if (number == TCP_CONNECTIONS_MAX) {
        return NET_ERR_SEND;
    }
    connection_t* c = &connections[number];
    memset(c, 0, sizeof(connection_t));
    c->listener = -1;                       // AerOS's own, not one that arrived on a listener
    c->buffer = malloc(TCP_BUFFER);
    if (c->buffer == NULL) {
        return NET_ERR_SEND;
    }

    // Our port and first sequence number are random, so a stranger can't guess them and slip a
    // made-up segment into the connection (or mistake an old connection's packets for this one's)
    uint8_t random[6];
    crypto_random(random, sizeof(random));
    c->used = true;
    c->state = CONNECTING;
    c->address = address;
    c->port = port;
    c->our_port = (uint16_t)(49152 + (get16(random) % 16384));   // the ports kept for this use
    uint32_t first = get32(random + 2);
    c->send_unacked = first;
    c->send_next = first + 1;               // a SYN counts as one byte

    TRACE("connecting to port %u from our port %u: sending SYN", port, c->our_port);
    outgoing_t syn = { FLAG_SYN, first, NULL, 0 };
    int result = send_and_wait(c, &syn, connected);
    if (result != NET_OK) {
        tcp_close(number);
        return result;
    }
    return number;
}

// open a connection (see tcp.h): open_connection, with the network lock held
int tcp_connect (uint32_t address, uint16_t port) {
    net_lock();
    int number = open_connection(address, port);
    net_unlock();
    return number;
}

// Send bytes, a segment at a time, each acknowledged before the next (tcp_send takes the
// network lock and calls this)
static int send_bytes (int number, const void* data, size_t length) {
    if (number < 0 || number >= TCP_CONNECTIONS_MAX || !connections[number].used) {
        return NET_ERR_CLOSED;
    }
    connection_t* c = &connections[number];
    const uint8_t* bytes = data;
    size_t sent = 0;
    while (sent < length) {
        if (c->reset) {
            return NET_ERR_CLOSED;
        }
        // as much as one segment can carry: their MSS, ours, and the room they have
        size_t piece = length - sent;
        size_t most = c->their_mss < OUR_MSS ? c->their_mss : OUR_MSS;
        if (c->their_window > 0 && c->their_window < most) {
            most = c->their_window;
        }
        if (piece > most) {
            piece = most;
        }
        outgoing_t out = { FLAG_ACK | FLAG_PSH, c->send_next, bytes + sent, piece };
        c->send_next += (uint32_t)piece;
        int result = send_and_wait(c, &out, all_acknowledged);
        if (result != NET_OK) {
            return result;
        }
        sent += piece;
    }
    return NET_OK;
}

// send bytes (see tcp.h): send_bytes, with the network lock held
int tcp_send (int number, const void* data, size_t length) {
    net_lock();
    int result = send_bytes(number, data, length);
    net_unlock();
    return result;
}

// Wait for bytes, and take them out of the buffer (tcp_receive takes the network lock and calls this)
static int receive_bytes (int number, void* out, size_t size, uint32_t ms) {
    if (number < 0 || number >= TCP_CONNECTIONS_MAX || !connections[number].used) {
        return NET_ERR_CLOSED;
    }
    connection_t* c = &connections[number];
    uint32_t start = clkuptms();
    for (;;) {
        net_poll();
        if (c->buffered > 0) {
            break;
        }
        if (c->reset) {
            return NET_ERR_CLOSED;
        }
        if (c->state == THEY_CLOSED) {
            return 0; // they have finished, and every byte has been read
        }
        if (clkuptms() - start >= ms) {
            return NET_ERR_NO_ANSWER;
        }
        net_pause();
    }

    // take what fits from the front of the buffer, and move the rest up
    size_t taken = c->buffered < size ? c->buffered : size;
    bool was_nearly_full = room(c) < OUR_MSS;
    memcpy(out, c->buffer, taken);
    memmove(c->buffer, c->buffer + taken, c->buffered - taken);
    c->buffered -= taken;
    // If the buffer was too full for them to send a whole segment, they are waiting: tell them
    // there is room again (a "window update"), or they would never send the rest
    if (was_nearly_full && room(c) >= OUR_MSS) {
        send_ack(c);
    }
    return (int)taken;
}

// wait for bytes (see tcp.h): receive_bytes, with the network lock held
int tcp_receive (int number, void* out, size_t size, uint32_t ms) {
    net_lock();
    int result = receive_bytes(number, out, size, ms);
    net_unlock();
    return result;
}

// Close a connection, sending FIN, and forget it (tcp_close takes the network lock and calls this)
static void close_connection (int number) {
    if (number < 0 || number >= TCP_CONNECTIONS_MAX || !connections[number].used) {
        return;
    }
    connection_t* c = &connections[number];
    // Say we have finished, if the connection got that far (past its handshake), and wait a
    // little for them to acknowledge it. Not long: whether or not they do, AerOS is done with it
    if (c->state != CONNECTING && c->state != ARRIVING && !c->reset) {
        outgoing_t fin = { FLAG_FIN | FLAG_ACK, c->send_next, NULL, 0 };
        c->send_next++;                      // a FIN counts as one byte
        uint32_t start = clkuptms();
        send_segment(c, &fin);
        while (!all_acknowledged(c) && !c->reset && clkuptms() - start < FIRST_WAIT_MS) {
            net_poll();
            net_pause();
        }
        TRACE("closed the connection to port %u", c->port);
    }
    forget(c);
}

// close a connection (see tcp.h): close_connection, with the network lock held
void tcp_close (int number) {
    net_lock();
    close_connection(number);
    net_unlock();
}

// Listen on AerOS's port `port` (tcp_listen takes the network lock and calls this)
static int start_listening (uint16_t port) {
    if (!net_up()) {
        return NET_ERR_NO_CARD;
    }
    // only on a port a serve rule allows: a program can't open one up itself (see firewall.h)
    if (!fw_port_served(port)) {
        TRACE("not listening on port %u: no serve rule allows it (FW SERVE)", port);
        return NET_ERR_BLOCKED;
    }
    if (listener_on(port) >= 0) {
        return NET_ERR_IN_USE;
    }
    for (int l = 0; l < TCP_LISTENERS_MAX; l++) {
        if (!listeners[l].used) {
            listeners[l].used = true;
            listeners[l].port = port;
            TRACE("listening on port %u: others may connect to it", port);
            return l;
        }
    }
    return NET_ERR_SEND;
}

// listen on a port (see tcp.h): start_listening, with the network lock held
int tcp_listen (uint16_t port) {
    net_lock();
    int listener = start_listening(port);
    net_unlock();
    return listener;
}

// Hand over a connection that has arrived on the listener and finished its handshake, after
// one look at what the card has collected (tcp_accept takes the network lock and calls this)
static int take_arrived (int listener, uint32_t* from) {
    if (listener < 0 || listener >= TCP_LISTENERS_MAX || !listeners[listener].used) {
        return NET_ERR_CLOSED;
    }
    net_poll();
    // of the connections ready to be taken, the one that arrived first: first come, first served
    int oldest = -1;
    for (int i = 0; i < TCP_CONNECTIONS_MAX; i++) {
        connection_t* c = &connections[i];
        // one that was reset before anyone took it (they gave up) is cleared away
        if (c->used && c->listener == listener && c->waiting && c->reset) {
            forget(c);
            continue;
        }
        // (one that has already sent its request and finished, THEY_CLOSED, is just as ready)
        bool open = c->state == OPEN || c->state == THEY_CLOSED;
        if (c->used && c->listener == listener && c->waiting && open) {
            // (int32_t)(a - b) < 0 is "a came before b", even when the time wraps round
            if (oldest < 0 || (int32_t)(c->arrived - connections[oldest].arrived) < 0) {
                oldest = i;
            }
        }
    }
    if (oldest < 0) {
        return NET_ERR_NO_ANSWER;
    }
    connection_t* c = &connections[oldest];
    c->waiting = false;
    if (from != NULL) {
        *from = c->address;
    }
    TRACE("accepted the connection from port %u", c->port);
    return oldest;
}

// take a connection that has arrived (see tcp.h): take_arrived, with the network lock held
int tcp_accept (int listener, uint32_t* from) {
    net_lock();
    int number = take_arrived(listener, from);
    net_unlock();
    return number;
}

// stop listening (see tcp.h). The connections still waiting to be accepted are forgotten without
// a word: their computers give up when nothing more comes
void tcp_unlisten (int listener) {
    net_lock();
    if (listener >= 0 && listener < TCP_LISTENERS_MAX && listeners[listener].used) {
        for (int i = 0; i < TCP_CONNECTIONS_MAX; i++) {
            if (connections[i].used && connections[i].listener == listener && connections[i].waiting) {
                forget(&connections[i]);
            }
        }
        TRACE("stopped listening on port %u", listeners[listener].port);
        listeners[listener].used = false;
    }
    net_unlock();
}
