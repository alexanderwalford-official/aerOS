# Updating AerOS

AerOS [installed on a disk](INSTALLING.md) is one file, `/boot/main.elf`. Updating it is
putting a newer `main.elf` in its place. The new one comes from the
[package server](PACKAGES.md), signed with the same key as the packages, and if it doesn't
start, GRUB goes back to the one from before by itself.

```
the maker:  make  ->  python3 tools/aerpkg.py publish-os src/main.elf
AerOS:      the update app  ->  osupdate(kernel, signature)  ->  restart  ->  the new AerOS
```

## The built-ins

| Built-in | What it does |
| --- | --- |
| `osbuild()` | this AerOS's build number: the time it was built, `"20261204183000"` (UTC, year first, so comparing two as text puts them in time order) |
| `osversion()` | this AerOS's version, `"0.01 ALPHA"` |
| `osupdatable()` | `true` if AerOS can be updated here (it is on a disk made by `INSTALL`), or `false` after saying why not |
| `osupdate(kernel, signature)` | checks a new `main.elf` (bytes, as `httpget` gives them) and its signature (128 hex digits), asks you, and puts it on the disk to start next time. `true` if it is in place, `false` after saying why not |

An update app follows the same steps as `pkg`:
1. fetch `/index.txt` and `/index.sig`, and `verify` the index;
2. find the `#os` line;
3. show `osversion()` and `osbuild()` beside the line's version and build, and stop if they're the same;
4. fetch `/os/main.elf`, and call `osupdate` with it and the line's signature.

AerOS does the checks that matter itself, so an app can't skip them.

## What osupdate() checks, before anything on the disk changes

- **Signed by AerOS's makers.** It checks the signature against the key built into AerOS
  (`src/sys/package_key.h`), not one the program hands it. So no program can install a
  `main.elf` of its own, whatever it says.
- **An AerOS kernel.** It must be an ELF file with a multiboot header, which GRUB needs to start
  it, and a build stamp.
- **Newer than the `main.elf` on the disk.** Each `main.elf` carries the time it was built,
  which the Makefile writes in every time it links (`obj/build_stamp.c`). Without this check, an
  old `main.elf` with a known bug, genuinely signed, could be sent again to take AerOS back to
  it. The packages' serial number does the same job for them.
- **Yes from the person at the keyboard.** AerOS asks with its own yellow question, then asks
  for the password, as `install()` does. It isn't the program that asks, and a program in the
  background can't call it.

The stamp is the text `AEROS-BUILD:20261204183000 0.02 ALPHA` with the byte 1 in front.
AerOS's own source is built into it (`/src/`), examples of the stamp included, and that byte
is never in text, so only the real stamp can match.

## What changes on the disk

| File | What |
| --- | --- |
| `/boot/old.elf` | the `main.elf` there was, kept |
| `/boot/main.elf` | the new one, read back and compared once written |
| `/boot/grub/grub.cfg` | this AerOS's own menu (`src/install/grub.cfg`), if the disk's is different |
| `/grubenv` | `aeros_update=new` (`src/sys/grubenv.h`; RES keeps its `aeros_mode` there too) |

If anything can't be written, it is undone, and AerOS starts as it did.

No program may change `/boot/` or `/grubenv` at all, however you answer: they are what starts
AerOS ([permissions](../src/disk/docs/perms.md)). At the prompt you still can, as with any file.

## Going back by itself

```
aeros_update=new    GRUB: set it to "tried" (save_env), start main.elf with the word "trial"
                    AerOS, once its file system is up: take it out. The update worked
aeros_update=tried  (the new one never got that far, and the computer was started again)
                    GRUB: start old.elf with the word "fallback"
                    AerOS (the one from before): copy itself back to main.elf, delete old.elf,
                    take the setting out, and say "[WARN] The update to build ... didn't start"
```

"Didn't start" means it never got as far as its file system, for example a kernel that stops
straight away or crashes while booting. One that starts but has a bug later on stays.
`/boot/old.elf` is still there until the next update, so it can be copied back by hand at the
prompt.

The new AerOS also brings the disk's GRUB menu up to date with its own, the first time it
starts. So a change to `src/install/grub.cfg` reaches installed disks with the next update.

## Publishing one

```
cd src && make
python3 tools/aerpkg.py publish-os src/main.elf
```

`publish-os` reads the build number and version from the stamp in the file and signs it. It
then adds an `#os` line to the index, or replaces the one there, and sends both to the server:

```
#os 20261204183000 5c0a9e71...d2b8 0.02 ALPHA
    ^              ^               ^
    build          signature       version (the rest of the line)
```

The server keeps it in a table of its own and serves it at `/os/main.elf`. It refuses an
upload whose `main.elf` doesn't match the `#os` line's signature, and `publish-os` won't send
a build older than the one the server has. Publishing packages afterwards keeps the `#os` line
as it is. The line starts with `#`, so a `pkg` that doesn't know about it skips it.

**Version or build?** Every build gets a new build number by itself, which is what updates go
by. The version (`AEROS_VERSION` in `src/sys/boot.h`) is only shown: change it when a release
deserves a new name.

## Limits

- **16 MB at most.** That is all `httpget` will fetch (`HTTP_BODY_MAX`), and `main.elf` is about
  2.7 MB now. It has the built-in apps, the manual and the source in it, so it grows with them.
  The server (`FILE_MAX`, `UPLOAD_MAX`), nginx (`client_max_body_size`) and `aerpkg.py` follow
  the same limit.
- **Only from an installed disk.** Started from the CD, AerOS is whatever is on the CD, so a
  newer CD is the update. `osupdatable()` says so.
- A kernel that starts but crashes later, or one that can't reach the network, isn't caught by
  going back (see above).

## Testing it

`tools/test/tests/test_update.py`:
- **The server side.** `publish-os` puts AerOS on a test server with an `#os` line and serves
  it, an older build is refused, and publishing a package afterwards keeps it.
- **The whole round in QEMU,** with kernels made from the one built: the test's key in place
  of the real one, and build numbers of the test's choosing.
  - It installs the first build on a blank disk.
  - A program trying to write `/boot/main.elf` is refused without being asked.
  - A wrong signature is refused.
  - The second build is fetched over the network and installed. It starts and says so, and
    the same build again is refused.
  - A third build that never starts (its first instruction jumps to itself) is installed.
    GRUB marks it `tried`, and the next start goes back to the second build, which puts itself
    back.

`t.boot(kernel=...)` starts a kernel other than the one built.
