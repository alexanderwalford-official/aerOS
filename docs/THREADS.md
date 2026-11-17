# Threads

A thread is a piece of AerOS running a function of its own, with its own stack. AerOS has one
processor, so only one thread runs at a time: they take turns, quickly enough that they seem to
run together. The code is [`src/sys/thread.c`](../src/sys/thread.c), and the comment at the top
of [`thread.h`](../src/sys/thread.h) is the short version.

This is stage 1: the threads themselves, and the first one that does real work (the disk
writer). Next come AerLang programs running in the background, and threads inside a program.

## The threads there are

| Thread | What it does |
| --- | --- |
| 0, `main` | everything AerOS did before there were threads: booting, the terminal, and every program it runs. It uses the stack `entry.asm` set up |
| `disk writer` | puts changes the [disk cache](DISK_CACHE.md) is holding onto the disk, a second after they were made, even while a busy program runs |

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

## Stacks

Each new thread gets 16 KB of stack, filled with the byte `0xA5` first. A stack grows
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

## What threads don't do (yet, or at all)

- **More than one processor.** It is still one core, taking turns. Using several (SMP) would be
  a large job of its own, and would bring back the need for locks.
- **Protection.** All threads share all memory: a thread that goes wrong can damage what others
  use. Keeping programs apart needs paging and real processes.
- **Stage 2:** AerLang programs as threads, so `bg` can run a program in the background while
  you keep using the prompt.
- **Stage 3:** `spawn(func)` and `join(t)` in AerLang, and a safe way for threads to pass values
  to each other.
