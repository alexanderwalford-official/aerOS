/*
    jobs.h - programs running in the background, while you carry on at the prompt

        bg counter          run counter.aer in the background: the prompt comes straight back
        bg                  list the programs running in the background
        stop 4              ask the one on thread 4 to stop (THR and BG show the numbers)

    Each runs on a thread of its own (sys/thread.h), in an AerLang session of its own, so it
    never mixes with the program in front or the prompt's variables. What a program in the
    background may do is a little less than one in front:

        the keyboard, the mouse, the screen, sound     no: they are the program in front's. A
                                                       program that names any of those built-ins
                                                       is refused before it starts
        asking before it touches your files            no: there is nobody to answer. Only what
                                                       you allowed with A (always) is allowed
        Ctrl+C                                         no: that is the program in front's. STOP
                                                       stops one in the background
        printing                                       yes: kept, and shown at the prompt (below)
        files, databases, the network, sleep, time     yes

    WHAT IT PRINTS

    A program in the background mustn't write over what is on the screen (a game, the editor, a
    line you are half way through typing). So what it prints goes into a buffer here instead
    (console_divert, drivers/video/VGA_linear.h), and the terminal shows it, each line marked
    with the program's name, at moments when that can't get in the way: just before a prompt,
    and while you are at a prompt with nothing typed yet (the prompt is shown again below).

    Logging out stops every program in the background (and throws away what they printed), so
    nothing of yours carries on running, or shows up, for the next person.
*/

#ifndef JOBS_H
#define JOBS_H

#include <stdint.h>
#include <stdbool.h>

#define JOBS_MAX         8                 // programs in the background at once
#define JOB_STACK_SIZE   (256 * 1024)      // each one's stack: as much as the prompt's own
#define JOBS_OUTPUT_SIZE 8192              // what they have printed, waiting to be shown

// Start the program `path` (as typed: "counter", or a path) with `args` in the background.
// Its thread's id, or -1 if JOBS_MAX are running already or there is no memory for another
int jobs_start (const char* path, const char* args);

// Ask the background program on thread `id` to stop: it stops at its next safe point, between
// two statements. False if no program in the background has that thread
bool jobs_stop (int id);

// stop every program in the background and wait until they have, then forget what they printed
// (logging out, sys/terminal/cmd_accounts.c)
void jobs_stop_all (void);

// how many programs are running in the background
int jobs_running (void);

// what BG on its own shows: one line for each program in the background
void jobs_list (void);

// Has a program in the background printed anything that hasn't been shown yet?
bool jobs_output_waiting (void);

// Show what the programs in the background have printed, and forget it (the terminal, at the
// prompt). Printed in light cyan, so it stands out from your own commands' output
void jobs_show_output (void);

// A program in the background is about to print about `bytes` characters: wait (letting the
// other threads run) until the buffer has room for them, so nothing it prints is lost. Called
// from print() (lang/bi_text.c) at a safe point, before anything is printed
void jobs_wait_for_room (uint32_t bytes);

// give each thread its own background program and printed text destination (thread_keep, see
// sys/thread.h). kernel.c calls it once, at boot
void jobs_keep_per_thread (void);

#endif /* JOBS_H */
