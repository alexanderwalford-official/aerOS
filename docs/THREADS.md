# Threads

A thread is a piece of AerOS running a function of its own, with its own stack. AerOS has one
processor, so only one thread runs at a time: they take turns, quickly enough that they seem to
run together. The code is [`src/sys/thread.c`](../src/sys/thread.c), and the comment at the top
of [`thread.h`](../src/sys/thread.h) is the short version.

So far there are two stages: the threads themselves, with the first one that does real work
(the disk writer), and AerLang programs running in the background with `BG`. Threads inside a
program come next.

## The threads there are

| Thread | What it does |
| --- | --- |
| 0, `main` | everything AerOS did before there were threads: booting, the terminal, and every program run at the prompt. It uses the stack `entry.asm` set up |
| `disk writer` | puts changes the [disk cache](DISK_CACHE.md) is holding onto the disk, a second after they were made, even while a busy program runs |
| one for each program run with `BG` | an AerLang program running in the background while you use the prompt ([below](#programs-in-the-background)) |

`THR` lists them:

```
tester@0//home/tester/: thr
ID  NAME             STATE      TIME (MS)  STACK USED
0   main             running         3670  (the boot stack)
3   disk writer      sleeping           1  324 of 16384 bytes
The processor has rested for 1368 ms, with nothing ready to run.
```

(The disk writer is thread 3 because threads 1 and 2 were the boot self-test's, below. Numbers
are never reused.)

## Switching from one thread to another

Each thread has its own stack. When a thread stops running, its registers go on top of its own
stack, and the thread remembers where that top is. `thread_switch` is the whole trick:

1. push this thread's registers onto its own stack;
2. remember the stack pointer;
3. load the other thread's stack pointer: now on its stack;
4. pop its registers, saved the same way when it last stopped;
5. `ret`: back to wherever that thread called `thread_switch` from.

So from a thread's point of view, `thread_switch` is a function call that simply returns,
maybe after the others have had their turns. Only the registers C expects a call to keep
(`ebx`, `esi`, `edi`, `ebp`) and the flags need saving. The x87 maths registers don't: C keeps
nothing in them across a call.

A new thread gets a stack made to look as if it had already stopped once, with
`thread_start` as the place to go back to. The first switch to it "returns" there, and
`thread_start` calls the thread's function.

## When threads take turns: safe points

This is the most important design choice. The timer counts each thread's turn (10 ms), but when
a turn is up **it doesn't stop the thread there and then**. It only raises a flag
(`thread_time_up`), and the thread steps aside at its next safe point:

| Safe point | Where |
| --- | --- |
| between two AerLang statements, and every time round a loop | `safe_point` in `src/lang/aer_interp.c` |
| while waiting for a key | `getk` in `src/drivers/input/IO.c`, through `thread_idle` |
| while sleeping | `thread_sleep`, which AerLang's `sleep()` uses |
| when a thread chooses to | `thread_yield` |

**Why:** most of AerOS was written for one thing running at a time. `malloc`, the file system,
the disk cache, drawing on the screen: each changes several things in a row, and is only
correct once it has finished. If a thread could be stopped half way through `malloc`, and the
next thread called `malloc` too, it would find the heap half changed, and memory would be
damaged in a way that shows up much later, somewhere else, only now and then. Preventing that
everywhere would need locks in every one of those parts.

With safe points, none of them is ever left half way through: a thread is only ever stopped
between two AerLang statements, or while it waits. So nothing needs a lock, and from the
outside it still looks like real multitasking: a busy program can't keep the others from
running. The cost is that C code that runs for a long time without a safe point (a big disk
read, say) keeps its turn until it finishes. That is the same as before there were threads.

**The one exception: the network.** Waiting for a web server can take seconds, and that wait is
in the middle of the network code. Holding everything up for it would mean a program fetching
a page in the background freezes the prompt, so the network's waits are safe points too: they
call `net_pause`, which is `thread_sleep(1)`. That makes the network the one place a thread can
be part way through something when another gets a turn, so it has AerOS's one lock
(`thread_lock`, see [below](#the-network-lock)).

## Whose turn: round robin

When a thread steps aside, the next one is found by looking through the threads in order,
starting after it, and taking the first that is ready. On the way, a sleeping thread whose time
is up, or one waiting for a thread that has finished, becomes ready. If nobody is ready
(everyone is asleep or waiting), the processor rests with `hlt` until the next interrupt, then
looks again. The timer's interrupt comes every millisecond, so a sleeper wakes on time.

## A thread's life

| State | Meaning |
| --- | --- |
| `ready` | waiting for its turn |
| `running` | running now (only ever one) |
| `sleeping` | in `thread_sleep`, until its time is up |
| `waiting` | in `thread_join`, until the thread it waits for has finished |
| `done` | its function returned |

`thread_create(name, func, arg)` starts one, and `thread_join(id)` waits for it to finish.
A finished thread's stack is given back to the heap.

## Each thread's own globals

Parts of AerOS keep "the one running now" in a global variable, written when only one thing
ever ran:

| Global | What it is | Where |
| --- | --- | --- |
| `aer` | the AerLang program (session) running | `src/lang/aer_interp.c` |
| `perm_running` | the program the file permission rules are for | `src/sys/perm.c` |
| `this_job` | the background program the thread is running | `src/sys/jobs.c` |
| `console_divert` | where printed text goes, instead of the screen | `src/drivers/video/VGA_linear.c` |
| `cd` | the current folder | `src/sys/f_sys.c` |
| `trace_watching` | whether `SHOW` is watching | `src/sys/trace.c` |

With several threads, each needs its own value of these. `thread_keep(&global, size)` makes
that so without changing any code that uses them: at every switch, `thread.c` copies the
globals' values into the stopping thread's own room, and copies the starting thread's back into
the globals. So each thread always finds its own values there. A new thread's start as 0.
`kernel.c` registers them all at boot.

## Programs in the background

```
tester@0//home/tester/: bg counter
counter is running in the background, as thread 4. What it prints shows up here; STOP 4 stops it.
tester@0//home/tester/: 6 * 7
42
[counter] tick 1
[counter] tick 2
tester@0//home/tester/: bg
ID  PROGRAM                          RUNNING FOR
4   counter                          3 s
STOP <id> stops one.
```

`BG program [args]` starts the program on a thread of its own (`src/sys/jobs.c`), with a 256 KB
stack (as much as the prompt's own, as AerLang can recurse deeply). `BG` on its own lists them,
`THR` shows their threads, and `STOP <id>` stops one at its next safe point.

**Its own session.** Each AerLang session is now a struct of its own: the prompt's, the one for
a program in front, and one for each program in the background. A session holds its variables,
its names and everything it has allocated, and `aer` (one of the globals above) points at the
one running. So a program in the background can't see or change the prompt's variables, or the
front program's, and when it ends only its own memory is freed.

**What it may do.** A little less than a program in front:

| | In the background |
| --- | --- |
| the keyboard, the mouse, the screen, sound | no: they belong to whoever is in front. A program that names any of those built-ins (`input`, `key`, `cls`, `gopen`, `play`...) is refused before it starts, with a message saying which |
| asking before touching your files | no: there is nobody to answer, so the answer is no, with a message saying why. Only what you allowed with A (always) is allowed: run it once in front to give that |
| Ctrl+C | no: that is the program in front's. `STOP` is how to stop one in the background |
| printing | yes: kept, and shown at the prompt (below) |
| files, databases, the network, `sleep`, `time` | yes. A database can't be open in two programs at once, and each program closes only its own when it ends |

**Its own folder.** A program keeps the folder it was started in (`cd` is per thread), so
changing folder at the prompt doesn't move where its files go.

**What it prints.** It mustn't write over what is on the screen: a game, the editor, or a line
you are half way through typing. So everything it prints goes into a buffer instead (through
`console_divert`), each line marked with its name. The terminal shows the buffer in light cyan
at moments when that can't get in the way: just before each prompt, and while you are at a
prompt with nothing typed yet (the prompt is rubbed out, the lines shown, and the prompt
written again below them). `print()` waits for room in the buffer first, so a program that
prints a lot slows down until its lines are shown, rather than losing them.

**Logging out** stops every program in the background, and throws away what they printed, so
nothing of yours runs, or shows up, for the next person.

**`nrecv`, `httpget`, `tcpaccept`, `tcprecv` and `sleep`** let the other threads run while they
wait, so a program in the background can wait for sealed messages, for a web server, or for
visitors to a web server of its own (`bg webserve 8080`, see [the network](NETWORK.md)), without
holding anything up.

## The network lock

The network code keeps its work in shared places: the buffer `poll()` reads frames into, the
one DNS question being waited for, the TCP connections. While one thread waits for an answer
and another has its turn, that second thread mustn't start a network job of its own in the
middle of the first's.

So the network has a lock (`thread_lock_t`, `src/sys/thread.c`). Every function `net.h`,
`tcp.h` and `http.h` offer that sends, collects or changes anything takes it first and lets go
at the end, and the work itself is in a static function next to it, so the lock is let go
whichever way the work returns:

```c
int http_get (const char* url_text, http_response_t* out) {
    net_lock();
    int result = fetch_following_redirects(url_text, out);
    net_unlock();
    return result;
}
```

- **Only one thread at a time holds it.** Another that wants it sleeps until it is let go, and
  every other thread still runs meanwhile. So a page fetched at the prompt while a program in
  the background is fetching one waits its turn, then comes back whole.
- **The thread holding it may take it again**, counting how many times. `http_get` calls
  `tcp_connect`, which calls `net_send_tcp`, and each takes the lock; only the last let go
  frees it.
- **Taking it can't be split.** Checking it is free and marking it taken happen with no safe
  point in between, so no other thread can slip in: on one processor, with threads only
  switching at safe points, that is all a lock needs.

`http_get` holds it for the whole request, redirects and all, so two web requests never mix.

## Stacks

Each new thread's stack (16 KB for threads written in C, 256 KB for programs in the
background) is filled with the byte `0xA5` first. A stack grows
downwards, so the `0xA5` bytes still at the bottom are ones it has never used: that is how THR
knows the most each thread has used. If even the very bottom has changed when a thread steps
aside, it went past its end into whatever memory is next to it. AerOS then stops with a message
saying which thread, rather than carry on with memory that may be damaged.

## Checking it works

- **At boot**, a self-test starts two threads that each make a note, give their turn away,
  make a note, sleep (the second one longer), and make a note, while main waits for both.
  Taking turns properly, the notes come in the order 1 2 1 2 1 2. The boot log shows
  `[ OK ] Threads self-test (taking turns, sleeping, waiting)`.
- **`tools/test/tests/test_threads.py`** runs a program that writes a file and then keeps the
  processor busy with an empty loop, and stops QEMU while it is still busy. The file is on the
  disk only if the disk writer had its turns during the loop. (Against the kernel from before
  threads, this test fails.)
- AerLang runs about 2% slower in a tight loop, from checking for a safe point every time
  round.
- **`tools/test/tests/test_background.py`** runs programs with `BG` and checks:
  - the prompt carries on while they run, and what they print shows up in order;
  - `BG`, `THR` and `STOP` work;
  - a program that needs the keyboard is refused;
  - a program can't change your files without an "always", and keeps its own folder;
  - a busy one doesn't hold up the prompt, and has its own variables;
  - a program in front and one in the background run at once, with their own databases, and
    Ctrl+C stops only the front one;
  - logging out stops them all;
  - while a program in the background fetches a page a slow server takes 6 seconds to send,
    the prompt still answers at once, and a page fetched at the prompt meanwhile waits its
    turn, then comes back whole. (With the network's pauses holding the processor, as before
    the lock, this test fails.)

## What threads don't do (yet, or at all)

- **More than one processor.** It is still one core, taking turns. Using several (SMP) would be
  a large job of its own, and would bring back the need for locks.
- **Protection.** All threads share all memory: a thread that goes wrong can damage what others
  use. Keeping programs apart needs paging and real processes.
- **One network job at a time.** The lock means two threads using the network take turns at
  it, a whole request each. Several at once would need the network code to keep each one's
  work apart.
- **Stage 3:** `spawn(func)` and `join(t)` in AerLang, and a safe way for threads to pass values
  to each other.
- **Sound in the background**, so `BG` could play music while you work. Sound belongs to the
  program in front for now.
