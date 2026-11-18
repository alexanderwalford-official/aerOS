# The network

AerOS's network does three jobs: sending short messages that can't be read or changed on the
way (sealed), fetching web pages over plain HTTP, and letting other computers connect to a
program in AerOS (a web server, say). Each only talks to computers you have allowed, and the
firewall starts with everything blocked. Sealed messages are never sent in the clear; TCP
always is, because web servers and browsers know nothing of seals (see "TCP and web pages").

```
+----------------------------------------+   +------------------------------------+
| your message  MSG / LSN, nsend / nrecv |   | a web page    GET, httpget()       |
+----------------------------------------+   | a server      tcplisten() and the  |
| sealed: AES-256-CTR + HMAC-SHA-256 tag |   |               rest, webserve       |
|                        sys/netseal.c   |   +------------------------------------+
+----------------------------------------+   | HTTP                 sys/http.c    |
| UDP, if a sealed rule allows it        |   +------------------------------------+
|                                        |   | TCP, both ways       sys/tcp.c     |
|                                        |   | out if a web rule allows it, in if |
|                                        |   | a serve rule does                  |
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
| `FW WEB <address or name> [port]` | allow fetching web pages from a computer (plain TCP out, port 80 unless given) |
| `FW SERVE <port> [address]` | allow other computers (anyone, or that one) to connect to AerOS's own port (plain TCP in) |
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

## Network cards

AerOS has one network driver, for the Intel PRO/1000 ("e1000") family. It looks for these on
the PCI bus as it starts (`is_e1000` in `drivers/net/e1000.c`); for what AerOS does with them
they all work the same way:

| PCI id | Card | Where | Tried |
| --- | --- | --- | --- |
| 8086:100E | 82540EM | QEMU's `e1000` (`make run`), VirtualBox's **Intel PRO/1000 MT Desktop** | QEMU, VirtualBox |
| 8086:100F | 82545EM | QEMU's `e1000-82545em`, VirtualBox's **Intel PRO/1000 MT Server** | QEMU, VirtualBox |
| 8086:1004 | 82543GC | VirtualBox's **Intel PRO/1000 T Server** | VirtualBox |

`NET` names the card it found (`Intel e1000 100f (PCI 00:11.0), link up`), and so does the
boot log. If there is a network card AerOS has no driver for, the boot log says which instead:

```
[INFO] AMD PCnet network card: no driver. In VirtualBox, pick Intel PRO/1000 MT Desktop
[INFO] Network card 10ec:8139 has no driver (AerOS supports the Intel e1000)
```

### VirtualBox

A new VirtualBox machine of type "Other" gets a **PCnet-FAST III** (AMD Am79C973, PCI id
1022:2000), which AerOS can't use. In Settings, Network, Adapter 1, Advanced, set **Adapter
Type** to **Intel PRO/1000 MT Desktop** (either server one works too). Then for "Attached to":

| Attached to | Works | AerOS's address |
| --- | --- | --- |
| **NAT** | out of the box | VirtualBox's NAT is the same as QEMU's: AerOS's usual `10.0.2.15`, gateway `10.0.2.2`, DNS `10.0.2.3` are right. Connections in need a port forwarding rule (Advanced, Port Forwarding) |
| **Bridged Adapter** | once AerOS has an address on your network | AerOS has no DHCP (see "What isn't sealed"), so set a free address on your network yourself, e.g. `NET IP 192.168.1.50/24 192.168.1.1 192.168.1.1` |

**Is a PCnet driver worth writing?** Not for now. VirtualBox offers three Intel cards that work,
QEMU's default is the e1000, and real PCnet cards are from the 1990s, so a PCnet driver would
only save changing one setting. It would be a second driver of about the e1000's size: the card
is set up through registers behind I/O ports (CSRs and BCRs) and an "initialisation block" in
memory, with its own ring layout. If one is ever written, `net.c` only needs a second set of
the functions it calls now (`e1000_init`, `_stop`, `_send`, `_receive`, `_mac`, `_name`,
`_link_up`) to choose between.

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

`FW WEB example.com` makes the second kind of firewall rule: a web rule, with no password. It
allows exactly one thing, AerOS opening TCP connections to that computer and port (80 unless
given). A web rule is marked `web` in `/acc/firewall` and has no key; `net_send` refuses to seal
a message for it, and `handle_udp` won't let a message in through it. The other way round,
`tcp_connect` and `tcp_handle` only accept web rules for connections AerOS opens.

`FW SERVE 8080` makes the third kind: a serve rule, also with no password. It is about AerOS's
own port, not another computer's: it lets other computers (anyone, or only the address given)
connect to AerOS's port 8080, where a program listens. It is kept as `serve 0.0.0.0 8080` in
`/acc/firewall` (0.0.0.0 is "anyone"). `fw_find`, which every sealed message and web connection
is checked with, never finds a serve rule; only `fw_serves` does. So the three kinds never stand
in for each other.

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
| listening | only on a port a serve rule allows. A SYN for it starts a connection (below); any other segment that isn't for one of AerOS's connections is blocked like any other packet, and `NET` shows which port it was for |

Like everything on the network it is polled: `tcp_handle` is called by `net.c` for each TCP
segment, but only while `tcp_connect`, `tcp_send`, `tcp_receive`, `tcp_close` or `tcp_accept` is
waiting or looking, and calling `net_poll`. `net.c` gives TCP four functions: `net_up`, `net_own_address`,
`net_send_tcp` (one segment in an IPv4 packet, via the same `send_ip` UDP uses) and `net_poll`.

### Listening, and a web server

```
fw serve 8080                   once: anyone may connect to AerOS's port 8080
bg webserve 8080 /docs/         serve the manual, in the background
```

then `http://<AerOS's address>:8080/` in a browser. `make run` passes TCP port 8080 on the
computer QEMU runs on through to AerOS's 8080 (`hostfwd=tcp::8080-:8080` in the Makefile's
`QEMU_NET`), so there it is `http://localhost:8080/`. (In a QEMU started without it, the
monitor, Ctrl+Alt+2, can add it while it runs: `hostfwd_add tcp::8080-:8080`.)

How a connection from outside arrives (`tcp_handle` and `arrive` in `sys/tcp.c`; the picture is
under LISTENING in `sys/tcp.h`):

1. A program calls `tcp_listen(8080)`, which notes the port, if a serve rule allows it. A
   program can't make a serve rule itself, so it can't open a port you haven't.
2. Another computer sends SYN to port 8080. If something listens there and a serve rule lets
   that computer in, `arrive` makes a new connection, ARRIVING, with a random first sequence
   number, and sends SYN + ACK.
3. Their ACK makes it OPEN. It waits, and anything they send (a request) goes into its buffer.
4. The program takes it with `tcp_accept` (the one that arrived first, if several are waiting),
   and from then on it is a connection like any other.

So that someone can't use up AerOS's eight connections by starting handshakes and never
finishing them (a "SYN flood"), at most 4 can wait to be accepted on each port, and one whose
handshake doesn't finish within 10 seconds is forgotten when the next SYN comes. Removing the
serve rule (`FW DEL`) blocks the port at once, for connections already open too.

In AerLang:

| | |
| --- | --- |
| `tcplisten(port)` | listen on AerOS's port: a number for `tcpaccept`, or `nil` (no serve rule, or something listens there already) |
| `tcpaccept(listener, ms)` | wait up to `ms` for a visitor: a connection number, or `nil` |
| `tcpconnect(host, port)` | connect to another computer's port, which a web rule must allow: a connection number, or `nil` |
| `tcprecv(connection, ms)` | what has arrived (up to 16 KB at a time), `""` once they have finished, or `nil` if nothing came in time |
| `tcpsend(connection, data)` | send a string or bytes: `true` once they have it all |
| `tcpclose(connection)` | finish a connection |
| `tcpunlisten(listener)` | stop listening |

The numbers belong to the program that got them, as `dbopen`'s do, and whatever a program
leaves open is closed when it ends. `tcpaccept` and `tcprecv` look once and then sleep, again
and again, so a server waiting for visitors holds the network lock only for each look: the rest
of AerOS can use the network meanwhile (see "The network lock" in [threads](THREADS.md)).
`SCAN` says a program "can let other computers connect to it".

`webserve` (a package: `pkg install webserve`, `packages/webserve.aer` in the repository) is a
web server in about 150 lines of AerLang, written
to be read:
- it looks after all its visitors at once, in one loop: a quick look for a new one
  (`tcpaccept` with 0 ms, which doesn't wait), then a quick look at what each has sent
  (`tcprecv` with 0 ms). Once a whole request is there (it ends with a blank line), it answers
  with a status line, a few headers and the file. Browsers open spare connections ahead of
  time that may say nothing for a while: one at a time, a page would wait behind those (it
  did: about 5 seconds), but in the loop they just wait their turn, and are let go after 10
  seconds of saying nothing;
- it shows `index.html` for `/`, or a list of the folder's files if there isn't one;
- it refuses any path with `..` in it, which would climb out of the folder;
- it prints a line for each request (`GET /index.md 200`), which shows up at the prompt.

It reads the folder the way any program does: AerOS's own folders freely, yours only where you
have allowed it (in the background, only with an "always").

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

While AerOS waits for an answer it pauses a millisecond at a time with `net_pause()`, which
lets other threads run (a program fetching a page in the background doesn't hold up the
prompt). So that two threads never mix their work, every function here that sends, collects or
changes anything takes the network lock first: one thread at a time is in the network, and
another waits its turn. See "The network lock" in [threads](THREADS.md#the-network-lock).

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
* **No HTTPS, IPv6, fragments or broadcast messages.** Web pages, and anything a server in AerOS
  sends, go in the clear, and a sealed message is one packet.
* **One piece at a time.** A server sends each piece of an answer only once the last is
  acknowledged: fine for pages, slow for big files. (Threads inside a program, still to come, would let each visitor have
  its own.)
* **Polling.** Nothing arrives in the background: a message that comes while AerOS is busy with
  something else waits in the card (32 frames at most) until AerOS next looks.
