# The network

AerOS's network does two jobs: sending short messages that can't be read or changed on the way
(sealed), and fetching web pages over plain HTTP. Each only talks to computers you have allowed,
and the firewall starts with everything blocked. Sealed messages are never sent in the clear;
web pages always are, because web servers know nothing of seals (see "TCP and web pages").

```
+----------------------------------------+   +------------------------------------+
| your message  MSG / LSN, nsend / nrecv |   | a web page    GET, httpget()       |
+----------------------------------------+   +------------------------------------+
| sealed: AES-256-CTR + HMAC-SHA-256 tag |   | HTTP                 sys/http.c    |
|                        sys/netseal.c   |   +------------------------------------+
+----------------------------------------+   | TCP, out only        sys/tcp.c     |
| UDP, if a sealed rule allows it        |   | ...if a web rule allows it         |
+----------------------------------------+---+------------------------------------+
| IPv4, ARP                                                      sys/net.c        |
+---------------------------------------------------------------------------------+
| Ethernet: the Intel e1000 card                       drivers/net/e1000.c        |
+---------------------------------------------------------------------------------+
```

## Using it

| Command | Does |
| --- | --- |
| `NET` | the card, AerOS's address, messages sent and received, and what was kept out |
| `NET IP 10.0.2.15/24 10.0.2.2 10.0.2.3` | set the address, gateway and DNS server (kept in `/acc/network`) |
| `FW` | the firewall's rules |
| `FW ALLOW <address or name> <port>` | allow one computer on one port, with a password both ends know |
| `FW DEL <n>` | remove a rule |
| `MSG <address or name> <port> <text>` | send a sealed message |
| `LSN <port>` | print the sealed messages that arrive on a port, until Esc |

In AerLang, `nsend(host, port, text)` gives `true` if the message was sent, and
`nrecv(port, ms)` waits up to `ms` milliseconds for one and gives it back, or `nil`.

On another computer, [`tools/aernet.py`](../tools/aernet.py) seals and opens messages the same
way (it needs Python's `cryptography` package):

```bash
python3 tools/aernet.py listen 5000 --echo        # print what arrives, and send it back
python3 tools/aernet.py send 127.0.0.1 5001 hello
```

`make run` gives QEMU an e1000 on its "user" network (`-nic user,model=e1000`), where the
computer QEMU runs on is `10.0.2.2`, and passes UDP port 5001 there on to AerOS's port 5000
(`hostfwd=udp::5001-:5000`). So in AerOS, `FW ALLOW 10.0.2.2 5000` (with the same password as
the script), then `MSG 10.0.2.2 5000 hello` reaches `listen 5000`, and `send 127.0.0.1 5001`
reaches `LSN 5000`.

## The firewall

A rule is an address (or a DNS name, looked up once when the rule is made), a port, and the key
made from its password. It allows:

* **sending** to that address, on that port (from the same port, so the answer comes back to it)
* **receiving** from that address, arriving on that port, and only if the message opens with
  the rule's key

Everything else that arrives is thrown away: packets from computers with no rule, to ports with
no rule, that aren't UDP (TCP, ICMP), that are fragments of bigger packets, and anything whose
seal doesn't open. `NET` counts them, and shows where the last blocked one came from.

The rules are kept in `/acc/firewall`, one a line:

```
# AerOS firewall: everything is blocked except these (see FW)
allow 10.0.2.2 5000 3f9c...e1
allow 104.20.23.154 5000 77a0...12 example.com
```

with each rule's key in hex. `/acc/` is the folder no program can ever read or change (see
[DIFFERENT.md](DIFFERENT.md), "Apps ask before they touch your files"), so a program can only
use the rules, never add one or read a key. Saving the rules zeros the old copy on the disk,
because it held keys.

There is deliberately no way to turn the firewall off: with no rule there is no key, and with no
key nothing could be sealed.

## Sealed messages

Both ends make the same key from the shared password, and two keys from that:

```
shared key = PBKDF2-HMAC-SHA-256(password, salt "AerOS network", 100 000 rounds)   (FW ALLOW)
AES key    = HMAC-SHA-256(shared key, "AerOS net key")
check key  = HMAC-SHA-256(shared key, "AerOS net mac")
```

A message travels as one UDP packet:

```
+------+-------+-----------------------------------+------+
| AERN | nonce | message, AES-256-CTR with AES key | tag  |
+------+-------+-----------------------------------+------+
   4      8      the message's length                32
```

The nonce is 8 new random bytes for every message (AES-CTR's counter block is the nonce and an
8 byte counter from 0), and the tag is HMAC-SHA-256 with the check key over everything before
it. A message is only opened after its tag has been checked, with a comparison that takes the
same time however much of it matches. This is the same encrypt-then-MAC design as encrypted
files ([SECURITY.md](SECURITY.md)).

A message can be up to 1428 bytes: a 1500 byte packet less 20 bytes of IPv4 header, 8 of UDP
and 44 of seal.

## TCP and web pages

`FW WEB example.com` makes the other kind of firewall rule: a web rule, with no password. It
allows exactly one thing, AerOS opening TCP connections to that computer and port (80 unless
given). A web rule is marked `web` in `/acc/firewall` and has no key; `net_send` refuses to seal
a message for it, and `handle_udp` won't let a message in through it. The other way round,
`tcp_connect` and `tcp_handle` only accept web rules. So the two kinds never stand in for each
other.

### TCP (`sys/tcp.c`)

The top of `sys/tcp.h` explains TCP with a picture of a whole connection. AerOS's TCP is the
smallest that is still correct on a real network:

| | |
| --- | --- |
| connecting | SYN with the MSS option, a random port (49152 and up) and a random first sequence number, sent again after 1, 2, 4, 8 and 16 seconds |
| sending | one segment at a time, each sent again until it is acknowledged (`send_and_wait`). Slow for big uploads, but AerOS only sends requests |
| receiving | in order only: an early segment (one after a lost one) is dropped and the last acknowledgement repeated, so the other side sends the gap again |
| the window | the room left in a 16 KB buffer, so what is on its way always fits in the card's 32 receive buffers. Reading makes room, and a window update tells the other side |
| closing | FIN, then a short wait for its acknowledgement. No TIME_WAIT: each connection has a new random port |
| never | listening: nothing can connect to AerOS. A segment that isn't for one of its own connections is blocked like any other packet |

Like everything on the network it is polled: `tcp_handle` is called by `net.c` for each TCP
segment, but only while `tcp_connect`, `tcp_send`, `tcp_receive` or `tcp_close` is waiting and
calling `net_poll`. `net.c` gives TCP four functions: `net_up`, `net_own_address`,
`net_send_tcp` (one segment in an IPv4 packet, via the same `send_ip` UDP uses) and `net_poll`.

### HTTP (`sys/http.c`)

`http_get(url, &response)` parses the address, sends `GET <path> HTTP/1.1` with
`Connection: close`, reads everything until the server closes the connection, and only then takes
the answer apart: the status line, the headers (`Content-Type`, `Content-Length`,
`Transfer-Encoding`, `Location`), and the body, put back together first if it was chunked.
Redirects (301, 302, 303, 307, 308) to another `http://` address are followed up to 5 times.
Pages are limited to 4 MB.

There is no TLS, so `https://` is turned away with a clear message, and a page can be read or
changed on the way. That is the right trade for things that are checked once they arrive (a
package with a signature, for example), and the wrong one for anything private.

## Inside the stack

`net_init()` finds the card at boot. The settings and rules are read from `/acc/` the first time
the network is used, after the file system has started.

There are no interrupts: the card puts arriving frames in memory by itself (32 buffers), and
`poll()` in `sys/net.c` takes them whenever AerOS is waiting for something: an ARP answer, a DNS
answer, or a message for `net_receive()`. Messages that arrive while waiting for something else
are kept in a small inbox (8 messages) until asked for.

Sending to an address: it goes straight to the address if it is on AerOS's own network (the
netmask says), otherwise to the gateway. Either way AerOS needs that card's MAC address, which
it asks for with ARP (three times, a third of a second each) and remembers (8 of them).

The e1000 driver uses the card's "legacy" descriptors: a receive ring of 32 and a send ring of 8,
each descriptor pointing at a 2 KB buffer. See the top of `drivers/net/e1000.c`.

## What isn't sealed, and why

* **ARP.** To send to an address on the local network AerOS has to ask which card has it, and
  it answers when someone asks for AerOS's address. ARP carries addresses, never data, and
  AerOS only asks when it is about to send something a rule allows.
* **DNS.** `FW ALLOW example.com 5000` (or `FW WEB example.com`) asks the DNS server for the address once, when the rule
  is made, and says so on the screen, because DNS has no encryption. Nothing else uses DNS:
  `MSG example.com 5000 hi` uses the address saved in the rule.
* **Web pages.** Everything sent and received over a web rule's TCP connections is plain text,
  as HTTP is: see "TCP and web pages" above.
* **No DHCP.** Asking the network for an address would mean sending before anything is
  allowed, so the address is set by hand (`NET IP`), with QEMU's usual one to start.

## What it doesn't do

* **No replay protection.** A sealed message recorded on the way and sent again is accepted
  again. Messages carry no sequence number or time.
* **No identity beyond the password.** Anyone who knows a rule's password can send as that
  computer, from its address. The salt is the same for every rule, so the same password gives
  the same key everywhere: use a different password for each rule.
* **Keys on the disk.** `/acc/firewall` holds the keys. Programs can't read it, but anyone at the
  terminal, or with the disk, can.
* **No HTTPS, IPv6, fragments, broadcast messages, or listening.** Web pages come in the clear,
  nothing can connect to AerOS, and a sealed message is one packet.
* **Polling.** Nothing arrives in the background: a message that comes while AerOS is busy with
  something else waits in the card (32 frames at most) until AerOS next looks.
