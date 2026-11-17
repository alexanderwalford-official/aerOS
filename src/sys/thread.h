/*
    thread.h - threads: several things in AerOS taking turns on the one processor

    A thread is a piece of AerOS running a function of its own, with its own stack. Only one
    runs at a time (there is one processor), and they take turns. Switching to another thread
    means saving where this one is (its registers, on its own stack) and picking up where the
    other one left off: each carries on exactly as if it had never stopped.

    WHEN THREADS TAKE TURNS: only at safe points

    The timer counts each thread's turn (THREAD_SLICE_MS). When a turn is up, it doesn't stop
    the thread there and then: it only raises a flag. The thread steps aside at its next safe
    point, a place where it isn't half way through changing anything the others use:

        between two AerLang statements             lang/aer_interp.c, exec()
        while waiting for a key                    getk() in drivers/input/IO.c
        while sleeping                             thread_sleep(), and sleep() in AerLang
        when it chooses to                         thread_yield()

    Kernel code in between (a disk write, malloc, drawing text) is never interrupted by another
    thread, so none of it needs locks. That is the reason for safe points: a thread switched in
    the middle of malloc could leave it half changed for the next thread to trip over.

    THE THREADS

        thread 0, "main"        everything AerOS did before threads: booting, the terminal, and
                                the programs it runs. It uses the stack entry.asm set up
        "disk writer"           puts changes the disk cache is holding onto the disk, at most a
                                second after they were made, whatever else is running
                                (sys/disk_cache.c)

    THR lists them. docs/THREADS.md explains how it all works.
*/

#ifndef THREAD_H
#define THREAD_H

#include <stdint.h>
#include <stdbool.h>

#define THREADS_MAX       16      // threads at once, counting main
#define THREAD_NAME_MAX   16      // the longest name, with its '\0'
#define THREAD_STACK_SIZE 16384   // each new thread's stack: 16 KB
#define THREAD_SLICE_MS   10      // a thread's turn, before it should let another have one

// what a thread is doing
typedef enum {
    THREAD_FREE,       // nothing: the slot is unused
    THREAD_READY,      // waiting for its turn
    THREAD_RUNNING,    // running now (only ever one)
    THREAD_SLEEPING,   // in thread_sleep, until wake_at
    THREAD_JOINING,    // in thread_join, until the thread it waits for is done
    THREAD_DONE        // its function returned
} thread_state_t;

// what THR shows about one thread
typedef struct {
    int id;
    char name[THREAD_NAME_MAX];
    thread_state_t state;
    uint32_t ms;            // how long it has run, in milliseconds
    uint32_t stack_used;    // the most of its stack it has used, in bytes (0 for main)
    uint32_t stack_size;    // its stack's size (0 for main, which has the boot stack)
} thread_info_t;

// set by the timer when the running thread's turn is up (see thread_check)
extern volatile bool thread_time_up;

// set threads up: the code running now becomes thread 0, "main" (kernel.c, once the heap and
// the timer are ready)
void thread_init (void);

// Start a thread running func(arg). It gets its first turn at the next safe point. Its id, or
// -1 if THREADS_MAX threads are running or there is no memory for its stack
int thread_create (const char* name, void (*func)(void* arg), void* arg);

// let another thread that is ready have a turn now, if there is one
void thread_yield (void);

// a safe point: if the running thread's turn is up, let another have one. Cheap otherwise
static inline void thread_check (void) {
    if (thread_time_up) {
        thread_yield();
    }
}

// stop for ms milliseconds, letting the other threads run meanwhile
void thread_sleep (uint32_t ms);

// Nothing to do until something happens (a key, a packet): let any thread that is ready run,
// or if none is, rest the processor until the next interrupt
void thread_idle (void);

// wait until thread id has finished. False if there is no such thread
bool thread_join (int id);

// the running thread's id (0 is main)
int thread_current (void);

// the timer, once a millisecond: counts the running thread's time and its turn (sys_clck.c)
void thread_tick (void);

// how long the processor has rested with no thread ready to run, in milliseconds
uint32_t thread_rest_ms (void);

// Fill in info for every thread that exists, up to max. How many were filled in
int thread_list (thread_info_t* info, int max);

// a state as a word for THR: "ready", "sleeping"...
const char* thread_state_name (thread_state_t state);

// Boot's self-test: two threads that take turns, sleep and finish, and main waits for them.
// True if they did all of that in the right order
bool thread_selftest (void);

#endif /* THREAD_H */
