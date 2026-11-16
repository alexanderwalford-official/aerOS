# The network

AerOS can send short messages to other computers, and get them back, but only to computers
you have allowed, and never in a form anyone else can read. It can also fetch web pages
(plain `http://`), again only from computers you have allowed.

## The firewall blocks everything

The firewall is always on, and starts with nothing allowed: AerOS sends nothing, and throws
away everything that arrives. You open it one computer and one port at a time:

```
fw allow 10.0.2.2 5000       allow 10.0.2.2 on port 5000
fw allow example.com 5000    or a computer by its name
fw                           what is allowed
fw del 1                     block the first one again
```

`fw allow` asks for a password. The other computer must use the same one: every message is
sealed with it (AES-256 encryption, and a tag that proves who sealed it and that it wasn't
changed). A message that arrives from an allowed computer but isn't sealed with the right
password is thrown away unread.

## Sending and receiving

```
msg 10.0.2.2 5000 hello      send a message
lsn 5000                     show messages as they arrive, until Esc
net                          the card, the address, and what was sent and kept out
```

`net` also says where the last blocked packet came from, which helps when working out what to
allow. In AerLang:

```
nsend("10.0.2.2", 5000, "hello")      # true if it was sent
let reply = nrecv(5000, 2000)         # wait up to 2 seconds: the message, or nil
```

## The other end

On another computer, `tools/aernet.py` from the AerOS source talks the same way:

```
python3 aernet.py listen 5000 --echo
python3 aernet.py send 127.0.0.1 5001 hello
```

In QEMU (`make run`), the computer QEMU runs on is `10.0.2.2`, and its port 5001 is passed on
to AerOS's port 5000.

## The address

AerOS doesn't ask the network for an address (that would mean sending something before
anything is allowed). It uses QEMU's usual one, 10.0.2.15, until you set another:

```
net ip 192.168.1.50/24 192.168.1.1 192.168.1.1     address, gateway, DNS server
```

## What isn't sealed

- **Finding a neighbour (ARP):** before sending, AerOS asks which network card has an address.
  That carries addresses only, never a message.
- **Looking up a name (DNS):** `fw allow example.com 5000` (or `fw web example.com`) asks the DNS server for the address
  once, when the rule is made, because DNS can't be encrypted. Nothing else uses DNS.
- **Web pages:** everything `get` and `httpget` send and receive is plain text, as the web is
  without HTTPS.

## Web pages

A web server knows nothing of seals, so it gets a different kind of rule, a web rule, with no
password:

```
fw web example.com                     allow fetching pages from example.com (port 80)
fw web 10.0.2.2 8080                   or from an address, on another port
get http://example.com/                show a page
get http://example.com/notes.txt n.txt save one in a file
```

`get` says how it went first: `200 OK, 1256 bytes, text/html` (200 is "here it is", 404 "no
such page"). A page that has moved is followed to its new address, if a web rule allows that
too. From a program, `httpget(url)` gives back the page's text, or nil.

Web pages aren't sealed: anything fetched can be read, or changed, on the way. That's fine for
public pages, and for files that are checked once they arrive, but don't fetch anything private
this way. A web rule only allows fetching pages, never sealed messages, and a rule with a
password never allows web pages: the two kinds don't stand in for each other.

## What it can't do (yet)

No HTTPS (so `https://` addresses don't work: try the `http://` one), nothing can connect to
AerOS (it only fetches), no IPv6, and a sealed message must fit in one packet: 1428 bytes. The
biggest page `get` will fetch is 4 MB. The card is an Intel e1000 (QEMU's own, and one
VirtualBox offers).

See also: [Accounts and encryption](security.md), [Ask AerOS why](why.md).
