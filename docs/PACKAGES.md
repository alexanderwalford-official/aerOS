# Packages

AerOS can install extra apps ("packages") from a package server, only when you ask, so the base
OS stays small. A package is one AerLang program. Everything about it is checked before it goes
on the disk: that it was signed by the packages' maker, and what it could do (SCAN).

```
the maker's computer                    the package server                   AerOS
--------------------                    ------------------                   -----
aerpkg.py publish chess.aer 1.2 ...     nginx                                pkg list
  fetches the index, checks it,           http://  (plain, for AerOS)  -->   pkg install chess
  changes chess's line, serial + 1,       https:// (+ /admin/publish)          checks every
  signs the program and the index   -->  server.py + SQLite database           signature against
private key (never leaves)                checks every upload, keeps it,       packagekey(), the
                                          serves it byte for byte              serial, SCANs, asks
```

The server never signs anything and doesn't have the private key: everything is signed on the
maker's computer and uploaded already signed. Taking the server over, or changing what it sends
on the way, can't get anything installed, because nobody else can make the signatures fit. So
AerOS fetches over plain HTTP (`src/sys/http.h`), which is all it can do.

## What the server serves

| Path | What it is |
| --- | --- |
| `/` | the landing page (`tools/pkgserver/index.html`), which lists the packages in a browser |
| `/index.txt` | the index: one line a package, `name version signature description` (see below) |
| `/index.sig` | the signature of `index.txt` itself: 128 hex digits, no line break |
| `/packages/<name>.aer` | each package's program |

The index looks like this:

```
# AerOS packages: name, version, signature of packages/<name>.aer, description
#serial 8
chess 1.2 8111c088...6b0e A game of chess against AerOS
^     ^   ^               ^
name  version             description (the rest of the line)
          the signature of packages/chess.aer: 128 hex digits
```

Lines starting with `#` are comments. A name is 1 to 8 of `a-z 0-9 _ -`, as it becomes
`/apps/<name>.aer` on AerOS's disk. The whole index is plain ASCII.

**The serial.** Every publish gives the index the next number on its `#serial` line. That line
is signed with the rest of the index, and `pkg` remembers the highest number it has installed
from, so it refuses an older index. Without the serial, someone in the way could send AerOS an
old index and an old program, both genuinely signed, to bring back a version with a known bug.
(The very first install on a new AerOS has nothing to compare with, so it takes whatever
genuine index it is given.)

## Publishing (on the maker's computer)

Once, make the key pair (see `tools/aersign.py`), and put the public key it prints into
`src/sys/package_key.h`:

```
python3 tools/aersign.py keygen ~/aeros-packages.key
```

Also once, make the token that lets you publish, and put the hash it prints into the server's
config file (see below):

```
python3 tools/aerpkg.py token ~/.aerpkg-token
```

The token isn't the signing key. It only stops strangers sending uploads: the server still
checks every upload's signatures, so someone with the token but not the key can't publish
anything.

Then, to add a package or a new version of one:

```
python3 tools/aerpkg.py publish ~/my-packages/chess.aer 1.2 A game of chess against AerOS
```

and to take one off:

```
python3 tools/aerpkg.py unpublish chess
```

Each one fetches the index the server has now, checks it is signed by your key, changes that
one line, gives it the next serial, signs the program and the new index, and sends them to
`https://packages.intercon.network/admin/publish`. `--server`, `--key` and `--token` change
where and with what (see `python3 tools/aerpkg.py`).

The private key never goes on the server or in the repository. Anyone who has it can make
packages every AerOS will install.

### Trying it on this computer first

Run a server here with a config of its own (as in "Running the server", with
`database = /tmp/aerpkg.db`, `port = 8000`, and `page` pointing at
`tools/pkgserver/index.html`):

```
python3 tools/pkgserver/server.py ~/aerpkg-test.conf
python3 tools/aerpkg.py publish chess.aer 1.2 A game of chess --server http://127.0.0.1:8000
```

In QEMU, this computer is `10.0.2.2`: set `SERVER` in `pkg.aer` to `http://10.0.2.2:8000`,
then `fw web 10.0.2.2 8000` and `pkg list` in AerOS. `tools/test/tests/test_packages.py` does
all of this with a test key.

`aerpkg.py build` and `serve` still make and serve a plain folder of files instead, with no
server program, for testing without the database.

## Running the server

`tools/pkgserver/` has everything that goes on the server:

| File | What it is |
| --- | --- |
| `server.py` | the server: Python's standard library and `cryptography`, about 380 lines, many of them comments |
| `server.conf.example` | its settings: where the database is, the public key, the token's hash |
| `aerpkg.service` | runs it with systemd, as its own user |
| `nginx.conf` | nginx in front of it: plain http for AerOS, https for publishing |
| `index.html` | the landing page |

`server.py` keeps everything in one SQLite database: the programs, the index and its signature,
and a line for every publish. It only listens on `127.0.0.1`. nginx passes it the public pages
on both `http://` and `https://`, and `/admin/publish` on `https://` only. There is **no
redirect from http to https**: AerOS can't follow one.

Before keeping an upload, it checks the token, that the index is signed by the packages' key,
that its serial is higher than the one being served, and that every program matches the
signature on its line. Only then does it change anything, all at once. The top of `server.py`
explains each check.

Setting it up on the server (Ubuntu, with nginx already running), from the repository's
`tools/pkgserver/` copied to the server:

```
# what it needs, and a user of its own to run as
sudo apt install python3-cryptography certbot python3-certbot-nginx
sudo useradd --system --no-create-home --shell /usr/sbin/nologin aerpkg

# the program, the page and the settings
sudo mkdir -p /opt/aerpkg /etc/aerpkg
sudo cp server.py index.html /opt/aerpkg/
sudo cp server.conf.example /etc/aerpkg/server.conf
sudo nano /etc/aerpkg/server.conf        # public_key and token_hash

# start it, and check it answers
sudo cp aerpkg.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aerpkg
curl http://127.0.0.1:8080/index.txt     # "Nothing has been published yet."

# a real certificate for https (certonly: it leaves nginx's settings alone, so no redirect)
sudo certbot certonly --nginx -d packages.intercon.network

# nginx: this site in place of the old folder of files
sudo cp nginx.conf /etc/nginx/sites-available/packages
sudo ln -s /etc/nginx/sites-available/packages /etc/nginx/sites-enabled/packages
sudo rm /etc/nginx/sites-enabled/<the old site>
sudo nginx -t && sudo systemctl reload nginx
```

Then publish from your computer as above: the first publish starts a new index at serial 1.
`journalctl -u aerpkg` shows what the server has been doing, refusals included, and the
database's `publishes` table keeps a line for every publish.

To update the server, copy the new `server.py` to `/opt/aerpkg/` and
`sudo systemctl restart aerpkg`. Back up `/var/lib/aerpkg/packages.db` now and then: it is
everything that is published (though it can always be published again from your computer).

## In AerOS

| What | Where |
| --- | --- |
| installed packages | `/apps/<name>.aer`, run by name like built-in apps (searched after `/bin/` and `/bin/games/`, so a package can't take a built-in app's place) |
| what is installed | `/apps/packages.txt`, one line each: `name version` |
| the highest serial installed from | `/apps/serial.txt` |
| the key packages are checked against | `packagekey()`, built in (`src/sys/package_key.h`), `""` until set |
| reaching the server | a web rule: `fw web packages.intercon.network 80`. A program can't make one, so the person does, once |

The built-ins the `pkg` app uses: `httpget(url)` (fetch), `verify(data, signature, key)`
(check), `scan(source)` (what a program could do), `rdf`, `wrf`, `apf`, `delf` (the files in
`/apps/`), and `args` (what was typed after `pkg`).

## What the pkg app must check, in order

Installing `chess`:

1. **There is a key:** `packagekey()` isn't `""`. If it is, nothing can be checked: stop.
2. **Fetch** `index.txt` and `index.sig`. If either is `nil`, the server can't be reached:
   suggest `fw web` for it.
3. **The index is genuine:** `verify(index, index_sig, packagekey())`. If not, stop, and say
   that the list of packages isn't signed by the packages' maker. Never use an unchecked index.
4. **The index isn't an old one:** its `#serial` isn't lower than the number in
   `/apps/serial.txt`. If it is, stop. (AerOS asks whether `pkg` may read `/apps/`; answering
   `A`, always, stops it asking again.)
5. **Find chess's line** in the index, and take its version and signature from it.
6. **Fetch** `packages/chess.aer`.
7. **The package is genuine:** `verify(program, signature, packagekey())`. If not, stop.
8. **Show what it could do:** `scan(program)`, both lists, and ask: install it? (y/n)
9. **Install:** `wrf("/apps/chess.aer", program)`, add `chess 1.2` to `/apps/packages.txt`,
   and write the index's serial to `/apps/serial.txt`. (AerOS asks whether `pkg` may change
   `/apps/`.)

Every check comes before anything is written, and a failed check stops everything.
