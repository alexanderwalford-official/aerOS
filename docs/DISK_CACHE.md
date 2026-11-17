# The disk cache

AerOS keeps the parts of the disk it used lately in memory, and puts changes onto the disk in
batches. This guide explains why, how it works, and what it costs. The code is
[`src/sys/disk_cache.c`](../src/sys/disk_cache.c), and the comment at the top of
[`disk_cache.h`](../src/sys/disk_cache.h) is the short version.

## Why

A disk is slow next to memory, and AerOS talks to it the simplest way there is: PIO
(`src/drivers/storage/ata.c`), where the CPU moves every byte through an I/O port itself. Before
the cache:

- **every read went to the disk**, even of something read a moment ago (only the last sector
  of the FAT was kept);
- **every write waited for a flush.** After each write AerOS sent the drive FLUSH CACHE and
  waited until the drive said the data was really stored. Flushing is the slowest thing a drive
  does, and saving one file took many writes: its data, its folder entry, the FAT, and the
  FAT's second copy, each flushed on its own.

## Where it sits

```
files (f_sys.c)  ->  FAT (fat.c)  ->  the disk cache  ->  the drive (ata.c)
```

Every read and write of the AEROS disk goes through the cache: `fat.c` calls
`disk_cache_read` and `disk_cache_write` where it used to call `drive_read` and `drive_write`.
Nothing above it changed. The cache starts once `fat.c` has mounted the disk; any other drive
is read and written directly, as before.

## How it works

**Slots.** The cache is 8192 slots (4 MB). Each holds one 512 byte sector of the disk, and
remembers which sector it is, whether it was used lately, and whether it was changed and not
written yet ("waiting").

**Finding a sector.** Looking through 8192 slots on every read would be slow, so slots are
sorted into buckets by the lowest bits of their sector's number. Neighbouring sectors land in
neighbouring buckets, so finding one nearly always means looking at a single slot.

**Making room: the clock.** When every slot is used and another sector is wanted, one has to
go, ideally one nobody has used for a while. A "clock hand" goes round the slots: a slot used
since the hand last passed gets a second chance (its flag is cleared and the hand moves on),
and the first slot without one is reused. Sectors in use keep getting their flag set again,
so they stay. A waiting slot is never thrown away: everything waiting is written first.

**Reading.** Each sector asked for is copied from its slot if it has one. A run of sectors
that aren't kept is read from the drive with one command, then kept for next time.

**Writing.** A write only changes the slot and marks it waiting. Later, `disk_cache_sync`
writes every waiting slot to the drive, then flushes **once**. If one sector changes many
times in a row (the FAT does, once per cluster of a growing file), only its last version is
written.

## When changes reach the disk

| When | Where |
| --- | --- |
| before every prompt | `src/sys/terminal/t_int.c` |
| while AerOS waits for a key | `getk()` in `src/drivers/input/IO.c` |
| a second after the first change, whatever is running | the disk writer thread, `disk_writer` in `disk_cache.c` ([threads](THREADS.md)) |
| a second after the first change, checked on every read and write | `sync_if_due` in `disk_cache.c` |
| when half the cache is waiting | `sync_if_due` |
| straight away, for the system log | `close_log` in `src/sys/syslog.c` |
| before shutting down or restarting | `src/sys/power.c` |
| `SYNC` at the prompt, `sync()` in a program | `cli_sync`, `bi_sync` |

## What it costs

If the power goes, or AerOS stops without shutting down, the changes still waiting are lost.
That is at most about a second and a half of work by a program that is still running (the disk
writer looks twice a second, for changes that have waited a second). Once AerOS is back
at the prompt or waiting for a key, nothing is waiting.

The log is written through on purpose: after a crash, it is what says what happened, so it
can't lose its own last entries in that crash.

The FAT file system has no journal, so a crash in the middle of saving a file could always
leave it half saved. With the cache, "the middle" can be up to a second long instead of a few
milliseconds. A program that must be sure, like a game saving progress, can call `sync()`.

## How much faster

`bench` (`src/disk/bin/bench.aer`) does the same work every run. These are typical numbers in
QEMU, with the disk image on Windows through WSL. The "after" times include `sync()`, so they
count getting everything onto the disk, not just into memory:

| | before | after |
| --- | --- | --- |
| a 256 KB file: write, and onto the disk | 3000 ms | 165 ms |
| reading it back | 135 ms | 2 ms |
| 50 files of 1 KB: write, and onto the disk | 850 ms | 38 ms |
| reading them back | 110 ms | 2 ms |
| 200 database inserts, and onto the disk | 4900 ms | 27 ms |
| everything | about 10 s | about 0.3 s |

On a real disk the numbers will differ (a flush there costs what the disk takes, not what the
computer QEMU runs on takes), but the reason for the difference is the same.

Reading back is fast here because the data was just written, so it is still kept. Something
not used since AerOS started still comes from the disk the first time.

## Seeing it

```
tester@0//home/tester/: sync
Nothing was waiting: every change is already on the disk.
Disk cache: 4096 KB, holding 1950 of 8192 sectors.
Reads found in memory: 100% (12216 of 12243 sectors).
Written to the disk: 1970 sectors, in 7 batch(es), each with one flush.
```

That was just after starting and running `bench` once. "Nothing was waiting" is usual: the
prompt had already written everything before it came back.

`show` doesn't list the cache's writing for a command, as that happens just after the command,
before the next prompt. Inside a long program it can show up, as "wrote 37 changed sector(s)
to the disk, then flushed it once".

## Tests

`tools/test/tests/test_disk_cache.py` stops QEMU abruptly (as if the power went) at each of
the points above, then reads the disk image from outside to check the change is there. It also
writes and reads back a 6 MB file, more than the cache holds, and checks every byte and the
FAT volume afterwards.

## Later

- **Threads.** Threads only switch at safe points ([THREADS.md](THREADS.md)), never in the
  middle of a function in the cache, so the disk writer thread never finds it half changed. If
  threads could ever be switched anywhere, the cache would need one lock around it, and that
  would be the only place disk access needs one.
- **Writing neighbours together.** Waiting sectors are written one command each. Joining
  neighbouring sectors into one command would save a little more, but the flush was the cost.
- **Its size.** 4 MB is a fixed choice (`DISK_CACHE_SECTORS`). It could grow with the memory
  the computer has.
