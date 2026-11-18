# Packages

AerOS can install extra apps ("packages") from a package server, only when you ask, so the base
OS stays small. A package is an AerLang program, and the files it comes with, if it has any (a
picture viewer's sample pictures, a video). Everything about it is checked before it goes on
the disk: that every part of it was signed by the packages' maker, and what its program could
do (SCAN).

The games, the web server and the sample pictures and video used to be built into AerOS. They
are packages now (see [The packages](#the-packages) below), which made AerOS about 390 KB
smaller: `pkg install snake` gets Snake back.

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
| `/packages/<name>/<file>` | the files a package comes with |

The index looks like this:

```
# AerOS packages: name, version, signature of packages/<name>.aer, description
#serial 8
chess 1.2 8111c088...6b0e A game of chess against AerOS
^     ^   ^               ^
name  version             description (the rest of the line)
          the signature of packages/chess.aer: 128 hex digits
viewer 1.0 41d0e2a7...90c1 A picture viewer (BMP), with two pictures to try it on
#file viewer aeros.bmp 9b7c11e0...2f4a
#file viewer colours.bmp 03aa5c8d...e771
      ^      ^         ^
      the package, the file, and the signature of packages/viewer/aeros.bmp
```

Lines starting with `#` are comments, except two kinds that `pkg` reads: `#serial` (below) and
`#file`, one for each file a package comes with. (They start with `#` so an older `pkg`, which
doesn't know about them, skips them.) A name is 1 to 8 of `a-z 0-9 _ -`, as it becomes
`/apps/<name>.aer` on AerOS's disk. The whole index is plain ASCII.

**The serial.** Every publish gives the index the next number on its `#serial` line. That line
is signed with the rest of the index, and `pkg` remembers the highest number it has installed
from, so it refuses an older index. Without the serial, someone in the way could send AerOS an
old index and an old program, both genuinely signed, to bring back a version with a known bug.
(The very first install on a new AerOS has nothing to compare with, so it takes whatever
genuine index it is given.)

## A package's files

A package's files go in a folder named after it, next to its program, both on the maker's
computer and in AerOS:

| On the maker's computer | In AerOS |
| --- | --- |
| `packages/viewer.aer` | `/apps/viewer.aer` |
| `packages/viewer/aeros.bmp` | `/apps/viewer/aeros.bmp` |
| `packages/viewer/colours.bmp` | `/apps/viewer/colours.bmp` |

- **Each is signed on its own**, the same way as the program, and `pkg` checks every one before
  anything is written. Its signature is on its `#file` line, and the index is signed, so a file
  can't be swapped for another, or added.
- **A file can't be a program.** Its name is an 8.3 name in lower case (`aeros.bmp`), never
  `.aer`: `pkg` shows you what a package's program could do before you say yes, and couldn't
  for programs tucked in among its files. `aerpkg.py`, the server and `pkg` all refuse one.
- **The program reads its own folder without asking** (see `src/sys/perm.h`): `/apps/viewer.aer`
  may read `/apps/viewer/`, as it may read its own folder. Changing files there still asks, as
  changing any file does.
- **It is the package's folder for good.** A package keeps what it makes there too (Snake its
  high scores, in `/apps/snake/scores.db`), and `pkg remove` takes the folder away with the
  program. `pkg update` writes the new version's files over the old ones, and leaves anything
  else in the folder alone.

Any file AerOS can fetch will do (up to 4 MB, `HTTP_BODY_MAX` in `src/sys/http.h`): `wrf()`
writes every byte of what `httpget()` gave, zeros and all.

## The packages

The repository's `packages/` folder holds the packages that used to be built in, ready to
publish, with `packages/packages.txt` listing each one's version and description:

| Package | What it is | Comes with |
| --- | --- | --- |
| `snake` | Snake, with high scores | |
| `maze3d` | a maze in 3D, seen from inside, with ray casting | |
| `move` | a square moved with the arrow keys: a small example of a game | |
| `guess` | guess the number from 1 to 100 | |
| `viewer` | a picture viewer (BMP) that zooms | `aeros.bmp`, `colours.bmp` |
| `clip` | plays a 6 second video, to try the video player on | `sample.mpg` |
| `webserve` | a small web server, in AerLang (see [the network](NETWORK.md)) | |
| `bench` | how fast AerOS reads and writes its disk | |
| `anim` | spinners, progress bars and moving text | |

The tests don't need the server for them: `t.package('snake')` puts one on a test's disk the
way `pkg install` would (`tools/test/run.py`).

**What a disk from an older AerOS does.** Its copies are still in `/bin/` and `/bin/games/`,
and as those are searched before `/apps/`, they would quietly run in place of an installed
package. So at boot AerOS deletes each file it once put there and no longer comes with, if it
is still exactly what it put there (`/sys/apps.lst` records that), and the empty
`/bin/games/` with them. One you have changed is yours: it stays, and the boot log says so.
See the top of `src/sys/apps.c`.

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

Its files, if it has any, are taken from the folder next to it named after it
(`~/my-packages/chess/`). To publish every package in a folder with a `packages.txt` at once,
such as the repository's `packages/`:

```
python3 tools/aerpkg.py publish-all packages
```

which sends only what isn't on the server already exactly as it is (an Ed25519 signature is
the same every time the same bytes are signed, so the lines show what has changed). Give a
package a new version in `packages.txt` whenever you change it, so `pkg update` offers it.

To take one off:

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
| a package's own folder | `/apps/<name>/`: the files it comes with, and what it keeps itself. Made by `pkg install`, deleted by `pkg remove` |
| what is installed | `/apps/packages.txt`, one line each: `name version` |
| the highest serial installed from | `/apps/serial.txt` |
| the key packages are checked against | `packagekey()`, built in (`src/sys/package_key.h`), `""` until set |
| reaching the server | a web rule: `fw web packages.intercon.network 80`. A program can't make one, so the person does, once |

The built-ins the `pkg` app uses: `httpget(url)` (fetch), `verify(data, signature, key)`
(check), `scan(source)` (what a program could do), `rdf`, `wrf`, `apf`, `delf` (the files in
`/apps/`), `mkd` and `deld` (a package's folder), and `args` (what was typed after `pkg`).

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
7. **The package is genuine:** `verify(program, signature, packagekey())`. If not, stop. The
   same for each of its files (its `#file` lines): a name `pkg` will write (8.3, not `.aer`),
   fetched from `packages/chess/<file>`, and verified with the signature on its line.
8. **Show what it could do:** `scan(program)`, both lists, and the files it comes with, and
   ask: install it? (y/n)
9. **Install:** `mkd("/apps/chess/")`, its files into it, then `wrf("/apps/chess.aer",
   program)` (so the program is never there without its files), add `chess 1.2` to
   `/apps/packages.txt`, and write the index's serial to `/apps/serial.txt`. (AerOS asks
   whether `pkg` may change `/apps/`. It may change the folder it has just made without being
   asked again: nothing of yours can be in it yet.)

Every check comes before anything is written, and a failed check stops everything.

Installing something that is already installed stops before step 1 and points to `pkg update`
(installing it again would give it a second line in `/apps/packages.txt`).

## Updating

`pkg update` updates every installed package, and `pkg update chess` updates just one. Both do
steps 1 to 4 once. Then, for each installed package:

- **not in the index any more:** it is left as it is (it still works; the maker has only stopped
  offering it);
- **the same version as installed:** nothing to do;
- **a different version:** steps 6 to 9 for it, asking about each one. Its line in
  `/apps/packages.txt` is replaced, not added to.

"Different", not "newer": the index is signed and can't be an old one (step 4), so the version
it lists is the one its maker means you to have, even if that is a step back from a broken
release. Comparing version numbers would also mean agreeing what "1.10" against "1.9" means.
