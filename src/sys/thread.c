/*
    thread.c - threads taking turns on the one processor

    thread.h says what threads are for and when they take turns. This is how.

    SWITCHING: thread_switch
    Each thread has its own stack. A thread that isn't running has its registers saved on the
    top of its own stack, and `esp` remembers where that top is. thread_switch(&old->esp,
    new->esp) is the whole trick, in a few instructions (the assembly below):

        1. push this thread's registers onto its own stack
        2. save the stack pointer in old->esp
        3. load new->esp: the stack pointer is now on the other thread's stack
        4. pop that thread's registers, saved the same way when it last switched away
        5. "ret": return to wherever that thread called thread_switch from

    So a thread calls thread_switch and, from its point of view, the call simply returns, maybe
    much later, after the others have had their turns. Only the registers C expects a function
    call to keep (ebx, esi, edi, ebp) and the flags need saving: C already assumes eax, ecx and
    edx may change across any call. The x87 maths registers don't need saving either: C keeps
    nothing in them across a function call, and threads only ever switch inside one.

    A NEW THREAD
    thread_create makes a stack that looks as if the thread had already switched away once:
    zeros for its saved registers, flags with interrupts on, and as the return address,
    thread_start. The first switch to it pops those and "returns" into thread_start, which calls
    the thread's function, and marks the thread done when that returns.

        top of the new stack:   0              (where thread_start would return to: it doesn't)
                                thread_start   (thread_switch's "ret" goes here)
                                0, 0, 0, 0     (ebp, ebx, esi, edi)
        esp points here ->      0x202          (the flags: bit 9 = interrupts on)

    WHOSE TURN: round robin
    pick_next looks at the threads one by one, starting after the one running, and takes the
    first that is ready. On the way, a sleeper whose time is up, or a thread waiting for one that
    has finished, becomes ready. When nobody is ready (everyone is asleep or waiting), the
    processor rests ("hlt") until the next interrupt, and looks again.

    THE TIMER
    thread_tick runs from the timer interrupt, once a millisecond. It counts the running thread's
    time and its turn, and raises thread_time_up when the turn is over. That is all it does: the
    interrupt never switches threads itself (see "safe points" in thread.h).

    STACKS
    A new thread's 16 KB stack is filled with 0xA5 first. A stack grows downwards, so the bytes
    still 0xA5 at the bottom are ones it never used: THR shows the most each thread has used.
    If the very bottom has changed when the thread switches away, it went past the end, into
    whatever memory comes before it, and AerOS stops with a message rather than carry on with
    memory damaged.
*/

#include "thread.h"
#include "sys_clck.h"
#include "power.h"
#include "../drivers/input/GIO.h"
#include "../drivers/video/VGA_linear.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdio.h"

// what an unused byte of a new thread's stack holds (see "stacks" above)
#define STACK_FILL 0xA5

// one thread
typedef struct {
    thread_state_t state;
    int id;                          // numbers count up and are never reused
    char name[THREAD_NAME_MAX];
    uint32_t esp;                    // where its registers are saved, while it isn't running
    uint8_t* stack;                  // its stack (NULL for main, and once a done thread's is freed)
    void (*func)(void* arg);         // what it runs, and what with
    void* arg;
    uint32_t wake_at;                // SLEEPING: the clkuptms() to wake at
    int waiting_for;                 // JOINING: the id of the thread it waits for
    volatile uint32_t ms;            // how long it has run (counted by thread_tick)
} thread_t;

static thread_t threads[THREADS_MAX];
static int current = 0;              // the running thread's slot in threads[]
static int next_id = 1;              // the id the next new thread gets (main is 0)
static bool started = false;         // thread_init has run
static volatile bool resting = false;      // the processor is resting: nobody is ready
static volatile uint32_t rest_ms = 0;      // how long it has rested, for THR
static volatile uint32_t slice_ms = 0;     // how long the running thread's turn has been

volatile bool thread_time_up = false;


// ---- switching -----------------------------------------------------------------------------------

// thread_switch(save, load): save this thread's registers on its stack and its stack pointer in
// *save, then load the stack pointer `load` and carry on as that thread (see the top of this file)
void thread_switch (uint32_t* save, uint32_t load);
__asm__(".pushsection .text\n"
        ".global thread_switch\n"
        "thread_switch:\n\t"
        "movl 4(%esp), %eax\n\t"   // save: where this thread's stack pointer goes
        "movl 8(%esp), %edx\n\t"   // load: the other thread's stack pointer
        "pushl %ebp\n\t"           // 1. this thread's registers, onto its own stack
        "pushl %ebx\n\t"
        "pushl %esi\n\t"
        "pushl %edi\n\t"
        "pushfl\n\t"
        "movl %esp, (%eax)\n\t"    // 2. remember where they are
        "movl %edx, %esp\n\t"      // 3. now on the other thread's stack
        "popfl\n\t"                // 4. its registers, saved when it switched away
        "popl %edi\n\t"
        "popl %esi\n\t"
        "popl %ebx\n\t"
        "popl %ebp\n\t"
        "ret\n"                    // 5. back to where it called thread_switch from
        ".popsection\n");

// Stop: a thread's stack ran past its end, so memory next to it may be damaged, and carrying on
// could do anything. Says which thread, and halts
static __attribute__((noreturn)) void stack_overflowed (const thread_t* t) {
    interrupts_disable();
    prtf("\nAerOS stopped: the thread \"%s\" used more than its %u byte stack.\n", t->name,
         (unsigned int)THREAD_STACK_SIZE);
    prtf("Memory next to it may be damaged, so it is safer to stop. Restart the computer.\n");
    sys_halt();
}

// has a thread finished? (one that can't be found finished long ago, and its slot was reused)
static bool finished (int id) {
    for (int i = 0; i < THREADS_MAX; i++) {
        if (threads[i].state != THREAD_FREE && threads[i].id == id) {
            return threads[i].state == THREAD_DONE;
        }
    }
    return true;
}

// The next thread to run: the first ready one after the running one, round the table. Sleepers
// whose time is up, and threads waiting for one that has finished, become ready on the way.
// -1 if none is ready
static int pick_next (void) {
    uint32_t now = clkuptms();
    for (int i = 1; i <= THREADS_MAX; i++) {
        int n = (current + i) % THREADS_MAX;
        thread_t* t = &threads[n];
        // (int32_t)(now - wake_at) >= 0 is "now is at or past wake_at", even when the
        // millisecond count wraps round past 0xFFFFFFFF
        if (t->state == THREAD_SLEEPING && (int32_t)(now - t->wake_at) >= 0) {
            t->state = THREAD_READY;
        }
        if (t->state == THREAD_JOINING && finished(t->waiting_for)) {
            t->state = THREAD_READY;
        }
        if (t->state == THREAD_READY) {
            return n;
        }
    }
    return -1;
}

// give back the stacks of threads that have finished (never the running one's: it is on it)
static void free_done_stacks (void) {
    for (int i = 0; i < THREADS_MAX; i++) {
        if (threads[i].state == THREAD_DONE && threads[i].stack != NULL && i != current) {
            free(threads[i].stack);
            threads[i].stack = NULL;
        }
    }
}

// The running thread has stopped running (it is ready, asleep, waiting or done: its state says
// which): switch to the next thread that is ready, resting the processor until one is. Comes
// back when it is this thread's turn again (never, for a thread that is done)
static void schedule (void) {
    thread_t* me = &threads[current];
    if (me->stack != NULL && me->stack[0] != STACK_FILL) {
        stack_overflowed(me);
    }
    thread_time_up = false;
    slice_ms = 0;

    int next;
    while ((next = pick_next()) < 0) {
        // nobody is ready: rest until an interrupt (the timer's wakes sleepers within 1 ms)
        resting = true;
        cpu_idle();
        resting = false;
    }
    threads[next].state = THREAD_RUNNING;
    if (next == current) {
        return; // it was this thread again (it had only been resting)
    }
    int was = current;
    current = next;
    thread_switch(&threads[was].esp, threads[next].esp);
    // this thread's turn again: whoever switched here set current back to it
    free_done_stacks();
}

// The first thing a new thread runs (see "a new thread" at the top): its function, then done
static void thread_start (void) {
    thread_t* me = &threads[current];
    me->func(me->arg);
    me->state = THREAD_DONE;
    schedule(); // never comes back: a done thread is never picked again
}


// ---- what the rest of AerOS calls (see thread.h) --------------------------------------------------

// the code running now becomes thread 0, main (see thread.h)
void thread_init (void) {
    memset(threads, 0, sizeof(threads)); // every slot THREAD_FREE (0)
    threads[0].state = THREAD_RUNNING;
    threads[0].id = 0;
    scpy(threads[0].name, "main", THREAD_NAME_MAX);
    current = 0;
    started = true;
}

// start a new thread (see thread.h)
int thread_create (const char* name, void (*func)(void* arg), void* arg) {
    if (!started) {
        return -1;
    }
    // a free slot, or one whose thread is done (its stack is given back first)
    int slot = -1;
    for (int i = 0; i < THREADS_MAX && slot < 0; i++) {
        if (threads[i].state == THREAD_FREE || threads[i].state == THREAD_DONE) {
            slot = i;
        }
    }
    if (slot < 0) {
        return -1;
    }
    thread_t* t = &threads[slot];
    free(t->stack);
    t->stack = malloc(THREAD_STACK_SIZE);
    if (t->stack == NULL) {
        t->state = THREAD_FREE;
        return -1;
    }
    memset(t->stack, STACK_FILL, THREAD_STACK_SIZE);

    // the stack as if it had switched away once (the picture at the top of this file)
    uint32_t* sp = (uint32_t*)(t->stack + THREAD_STACK_SIZE);
    *--sp = 0;                          // where thread_start would return to (it doesn't)
    *--sp = (uint32_t)thread_start;     // thread_switch's "ret" goes here
    *--sp = 0;                          // ebp
    *--sp = 0;                          // ebx
    *--sp = 0;                          // esi
    *--sp = 0;                          // edi
    *--sp = 0x202;                      // the flags: interrupts on (bit 1 is always 1)
    t->esp = (uint32_t)sp;

    t->id = next_id++;
    scpy(t->name, name, THREAD_NAME_MAX);
    t->func = func;
    t->arg = arg;
    t->ms = 0;
    t->state = THREAD_READY;
    return t->id;
}

// let another thread have a turn (see thread.h)
void thread_yield (void) {
    // before thread_init, or with interrupts off (the processor could never wake from resting),
    // there is nobody to switch to
    if (!started || !interrupts_enabled()) {
        thread_time_up = false;
        return;
    }
    threads[current].state = THREAD_READY;
    schedule();
}

// sleep, letting the others run (see thread.h)
void thread_sleep (uint32_t ms) {
    // without interrupts or a ticking timer nothing would wake the thread up: wait in place
    if (!started || !interrupts_enabled() || !clck_ready) {
        sleep_ms(ms);
        return;
    }
    threads[current].wake_at = clkuptms() + ms;
    threads[current].state = THREAD_SLEEPING;
    schedule();
}

// nothing to do until an interrupt (see thread.h)
void thread_idle (void) {
    if (started && pick_next() >= 0) {
        thread_yield(); // someone else can use the time
        return;
    }
    resting = true;
    cpu_idle();
    resting = false;
}

// wait for a thread to finish (see thread.h)
bool thread_join (int id) {
    if (!started || !interrupts_enabled() || id == threads[current].id || id < 0 || id >= next_id) {
        return false;
    }
    if (!finished(id)) {
        threads[current].waiting_for = id;
        threads[current].state = THREAD_JOINING;
        schedule();
    }
    // nobody else can wait for it now it is done: its slot is free for the next thread
    for (int i = 0; i < THREADS_MAX; i++) {
        if (threads[i].state == THREAD_DONE && threads[i].id == id) {
            free(threads[i].stack);
            threads[i].stack = NULL;
            threads[i].state = THREAD_FREE;
        }
    }
    return true;
}

// the running thread's id (see thread.h)
int thread_current (void) {
    return threads[current].id;
}

// the timer, once a millisecond (see thread.h). From the interrupt: it only counts
void thread_tick (void) {
    if (!started) {
        return;
    }
    if (resting) {
        rest_ms++;
        return;
    }
    threads[current].ms++;
    if (++slice_ms >= THREAD_SLICE_MS) {
        thread_time_up = true;
    }
}

// how long the processor has rested, with nobody ready to run (see thread.h)
uint32_t thread_rest_ms (void) {
    return rest_ms;
}

// how many bytes of a stack have been used: the ones that aren't STACK_FILL any more, counting
// from the bottom up to the first that changed
static uint32_t stack_used (const uint8_t* stack) {
    uint32_t untouched = 0;
    while (untouched < THREAD_STACK_SIZE && stack[untouched] == STACK_FILL) {
        untouched++;
    }
    return THREAD_STACK_SIZE - untouched;
}

// every thread there is, for THR (see thread.h)
int thread_list (thread_info_t* info, int max) {
    int count = 0;
    for (int i = 0; i < THREADS_MAX && count < max; i++) {
        thread_t* t = &threads[i];
        if (t->state == THREAD_FREE) {
            continue;
        }
        info[count].id = t->id;
        scpy(info[count].name, t->name, THREAD_NAME_MAX);
        info[count].state = t->state;
        info[count].ms = t->ms;
        info[count].stack_size = t->stack != NULL ? THREAD_STACK_SIZE : 0;
        info[count].stack_used = t->stack != NULL ? stack_used(t->stack) : 0;
        count++;
    }
    return count;
}

// a state as a word (see thread.h)
const char* thread_state_name (thread_state_t state) {
    switch (state) {
        case THREAD_READY:    return "ready";
        case THREAD_RUNNING:  return "running";
        case THREAD_SLEEPING: return "sleeping";
        case THREAD_JOINING:  return "waiting";
        case THREAD_DONE:     return "done";
        default:              return "free";
    }
}


// ---- the self-test ---------------------------------------------------------------------------------

// which test thread did something, in the order they did it
static int test_order[8];
static int test_count = 0;

// note that test thread `who` (1 or 2) got this far
static void test_note (int who) {
    if (test_count < 8) {
        test_order[test_count] = who;
    }
    test_count++;
}

// A test thread: a note, a turn given away, a note, a sleep (thread 2 sleeps longer), a note.
// Taking turns properly, the two make their notes in the order 1 2 1 2 1 2
static void test_thread (void* arg) {
    int who = (int)(uintptr_t)arg;
    test_note(who);
    thread_yield();
    test_note(who);
    thread_sleep((uint32_t)who * 5);
    test_note(who);
}

// two threads take turns, sleep and finish while main waits for them (see thread.h)
bool thread_selftest (void) {
    test_count = 0;
    int one = thread_create("test 1", test_thread, (void*)1);
    int two = thread_create("test 2", test_thread, (void*)2);
    if (one < 0 || two < 0) {
        return false;
    }
    thread_join(one);
    thread_join(two);
    static const int expected[6] = { 1, 2, 1, 2, 1, 2 };
    if (test_count != 6) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        if (test_order[i] != expected[i]) {
            return false;
        }
    }
    return true;
}
