# How AerOS works

AerOS is small on purpose: small enough to understand completely, and fast because it does
so little. It leaves out what bigger systems need for many programs at once:

| Left out | Instead | So |
| --- | --- | --- |
| virtual memory | every address is a real one | nothing to set up or look up |
| multitasking | one thing at a time, to the end | no switching, no locks |
| loadable drivers | every driver is built in | the OS is one file, about 1.8 MB |

The cost: a bug anywhere can stop everything. That is a fair trade for a system meant for
learning and building on.

## Starting up

```
GRUB         loads AerOS at 1 MB and sets up the screen
entry.asm    makes a stack, calls main()
kernel.c     screen, interrupts, timer, keyboard, then switches interrupts on
splash       the logo and the startup sound (X for boot options)
init_boot    the boot log: crypto self-test, sound card, disk, built-in files
terminal     the login screen, then the prompt, until you turn it off
```

## Memory

```
0 MB     +--------------------+
         | BIOS, VGA text     |
1 MB     +--------------------+
         | AerOS (code, data, |
         | 256 KB stack,      |
         | 4 MB of heap)      |
         +--------------------+
         | the rest of the    |  added to the heap at start up
         | memory             |
         +--------------------+
```

`MEM` shows how much is in use.

## Interrupts

The CPU stops whatever it is doing when hardware needs attention:

| What | When | AerOS |
| --- | --- | --- |
| the timer | 1000 times a second | counts milliseconds |
| the keyboard | each key press | stores the key |
| a CPU error | divide by zero... | shows the red error screen |

When there is nothing to do, AerOS halts the CPU until the next interrupt, so it uses
almost no power sitting at the prompt.

## Threads

A thread is a piece of AerOS running on its own. There is one processor, so threads take
turns: about every hundredth of a second, the running one lets the next have a go. `thr`
lists them. One is `main`, which runs the terminal and your programs; another is the disk
writer (below), which puts your changes on the disk even while a program is busy.

A thread only lets the next one go at a safe point: between two lines of an AerLang program,
or while it waits for a key or sleeps. Never in the middle of saving a file or drawing on the
screen. That keeps everything simple and safe, as nothing is ever left half done.

## The disk cache

Talking to a disk is slow next to memory, and the slowest part is asking the disk to make
sure what it was given is really stored (a "flush"). So AerOS keeps the last 4 MB of the disk
it used in memory:

- **Reading** something used lately comes from memory instead of the disk.
- **Writing** changes the copy in memory first. The changes are put on the disk together, with
  one flush for all of them: before every prompt, while AerOS waits for a key, within a second
  or so while a program runs (the disk writer thread), and before shutting down.

So when the prompt comes back, everything you did is on the disk. Only if the power goes in
the middle of a program can its last second of changes be lost. AerOS's log is the exception:
it is written straight away, as it is what says what happened after a crash. `sync` puts
everything onto the disk at once and shows the cache's numbers; `sync()` does it from a
program. `bench` times reading and writing, to see the difference.

## The parts

| Part | Source |
| --- | --- |
| the terminal and its commands | `src/sys/terminal/` (`t_int.c`, and `cmd_*.c` by topic) |
| files: disk (FAT) or memory | `src/sys/f_sys.c`, `src/sys/fat.c` |
| the disk cache | `src/sys/disk_cache.c` |
| threads | `src/sys/thread.c` |
| accounts and encryption | `src/sys/accounts/`, `src/sys/fcrypt.c` |
| AerLang, and AerLang at the prompt | `src/lang/` |
| WHY and SRC, and the `/src/` folder | `src/sys/explain.c`, `src/sys/source.c` |
| old versions of files | `src/sys/versions.c` |
| programs asking first | `src/sys/perm.c` |
| drivers | `src/drivers/` |
| apps and this manual | `src/disk/` (laid out as they are in AerOS) |

Every source file starts with an explanation of what it does, and every one of them is on
your computer too, in `/src/` (about half of that 1.8 MB is AerOS's own source code). So
`read /src/sys/fat.c` reads the disk driver, `src del` shows the code that runs a command,
and `why fs_list` explains a function from the comment above it: see
[Ask AerOS why](why.md). The full guide, with recipes for adding commands, AerLang functions
and drivers, is `docs/ARCHITECTURE.md` in the AerOS repository (the guides sit next to the
source code there, so they aren't in `/src/`).
