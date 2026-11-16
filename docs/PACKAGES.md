# Packages

AerOS can install extra apps ("packages") from a package server, only when you ask, so the base
OS stays small. A package is one AerLang program. Everything about it is checked before it goes
on the disk: that it was signed by the packages' maker, and what it could do (SCAN).

```
the maker's computer                 the package server              AerOS
--------------------                 ------------------              -----
packages/chess.aer   --aerpkg.py-->  index.txt                -->    pkg list / pkg install chess
packages/packages.txt   (signs)      index.sig                       checks every signature
private key (never leaves)           packages/chess.aer              against packagekey(),
                                     (plain files, nothing secret)   SCANs, asks, installs
```

The server is any plain web server (HTTP, `src/sys/http.h`): it only hands out files, and
holds nothing secret. Taking it over, or changing what it sends on the way, can't get anything
installed, because nobody else can make the signatures fit.

## On the server

| File | What it is |
| --- | --- |
| `index.txt` | one line a package: `name version signature description` (see below) |
| `index.sig` | the signature of `index.txt` itself: 128 hex digits, no line break |
| `packages/<name>.aer` | each package's program |

A line of `index.txt`, split at its first three spaces:

```
chess 1.2 8111c088...6b0e A game of chess against AerOS
^     ^   ^               ^
name  version             description (the rest of the line)
          the signature of packages/chess.aer: 128 hex digits
```

Lines starting with `#` are comments. A name is 1 to 8 of `a-z 0-9 _ -`, as it becomes
`/apps/<name>.aer` on AerOS's disk.

## Publishing (on the maker's computer)

Once, make the key pair (see `tools/aersign.py`), and put the public key it prints into
`src/sys/package_key.h`:

```
python3 tools/aersign.py keygen ~/aeros-packages.key
```

Then for each release, with the programs and a `packages.txt` in a folder of their own:

```
python3 tools/aerpkg.py build my-packages/ ~/aeros-packages.key site/
```

and copy everything in `site/` to the server. To try it first, `python3 tools/aerpkg.py serve
site/` serves it from this computer, which QEMU's AerOS reaches as `http://10.0.2.2:8000`
(after `fw web 10.0.2.2 8000`).

The private key never goes on the server or in the repository. Anyone who has it can make
packages every AerOS will install.

## In AerOS

| What | Where |
| --- | --- |
| installed packages | `/apps/<name>.aer`, run by name like built-in apps (searched after `/bin/` and `/bin/games/`, so a package can't take a built-in app's place) |
| what is installed | `/apps/installed.txt`, one line each: `name version` |
| the key packages are checked against | `packagekey()`, built in (`src/sys/package_key.h`), `""` until set |
| reaching the server | a web rule: `fw web packages.intercon.network`. A program can't make one, so the person does, once |

The built-ins the `pkg` app uses: `httpget(url)` (fetch), `verify(data, signature, key)`
(check), `scan(source)` (what a program could do), `rdf`, `wrf`, `delf`, `isf` (the files in
`/apps/`), and `args` (what was typed after `pkg`).

## What the pkg app must check, in order

Installing `chess`:

1. **There is a key:** `packagekey()` isn't `""`. If it is, nothing can be checked: stop.
2. **Fetch** `index.txt` and `index.sig`. If either is `nil`, the server can't be reached:
   suggest `fw web` for it.
3. **The index is genuine:** `verify(index, index_sig, packagekey())`. If not, stop, and say
   that the list of packages isn't signed by the packages' maker. Never use an unchecked index.
4. **Find chess's line** in the index, and take its version and signature from it.
5. **Fetch** `packages/chess.aer`.
6. **The package is genuine:** `verify(program, signature, packagekey())`. If not, stop.
7. **Show what it could do:** `scan(program)`, both lists, and ask: install it? (y/n)
8. **Install:** `wrf("/apps/chess.aer", program)`, and add `chess 1.2` to
   `/apps/installed.txt`. (AerOS asks the first time whether `pkg` may change `/apps/`.)

Every check comes before anything is written, and a failed check stops everything.
