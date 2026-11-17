/*
    jobs.c - programs running in the background (see jobs.h for what they may do)

    A JOB
    BG fills in a job_t (which program, what was typed after it, the folder it was started in)
    and starts a thread for it (sys/thread.h) with a 256 KB stack, the same as the prompt has, as
    AerLang can recurse deeply. The thread runs run_job: it sets up its own folder and where its
    printed text goes, runs the program with aer_run_file_background (lang/aer_interp.c), adds a
    last line saying it ended, and finishes.

    ITS OWN FOLDER, ITS OWN OUTPUT
    Each thread has its own value of a few globals (thread_keep, sys/thread.h). Three of them are
    set up here: this_job (which job the thread is running, NULL for any other thread), the
    current folder (f_sys.c's cd: a program started in /home/alex/ keeps using it, even after
    you change folder at the prompt), and console_divert (where its printed text goes).

    WHAT IT PRINTS
    console_divert hands every character a background program prints to keep_output, which puts
    it in `output`, a ring buffer: characters go in at the end and come out at the start, and
    both wrap round to the beginning of the array when they reach its end. Each line gets the
    program's name in front of it, so you can tell which program said what:

        [counter] 3 seconds

    The terminal takes them out and shows them (jobs_show_output) before each prompt, and while
    you are at an empty prompt. print() waits for room first (jobs_wait_for_room), so a program
    that prints a lot slows down until its lines are shown, rather than losing them.
*/

#include "jobs.h"
#include "thread.h"
#include "sys_clck.h"
#include "f_sys.h"
#include "../lang/aerlang.h"
#include "../drivers/video/VGA_linear.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdio.h"

#define ARGS_MAX 128   // the longest arguments kept for a program

// one program in the background
typedef struct {
    bool used;
    int thread;                     // its thread's id (THR and BG show it, STOP takes it)
    char path[FS_PATH_MAX];         // the program, as typed after BG ("counter")
    char args[ARGS_MAX];            // what was typed after it
    char folder[FS_PATH_MAX];       // the folder it was started in: its own current folder
    char name[THREAD_NAME_MAX];     // its name, for THR and in front of its lines ("counter")
    uint32_t started;               // clkuptms() when it started
    bool line_start;                // the next character it prints starts a new line
} job_t;

static job_t jobs[JOBS_MAX];

// the job the running thread is running, NULL for any other thread (each has its own: see the top)
static job_t* this_job = NULL;

// what programs in the background have printed, waiting to be shown (see "what it prints")
static char output[JOBS_OUTPUT_SIZE];
static uint32_t output_start = 0;   // where the oldest waiting character is
static uint32_t output_count = 0;   // how many are waiting
static uint32_t output_lost = 0;    // how many didn't fit, since the last time it was shown


// ---- what it prints ------------------------------------------------------------------------------

// put one character at the end of the waiting output (or count it lost, if there's no room)
static void output_add (char c) {
    if (output_count >= JOBS_OUTPUT_SIZE) {
        output_lost++;
        return;
    }
    output[(output_start + output_count) % JOBS_OUTPUT_SIZE] = c;
    output_count++;
}

// Where a background program's printed text goes (console_divert): into `output`, with its
// name in front of each new line
static void keep_output (char c) {
    job_t* job = this_job;
    if (job == NULL) {
        return;
    }
    if (job->line_start) {
        output_add('[');
        for (const char* p = job->name; *p != '\0'; p++) {
            output_add(*p);
        }
        output_add(']');
        output_add(' ');
        job->line_start = false;
    }
    output_add(c);
    if (c == '\n') {
        job->line_start = true;
    }
}

// is anything waiting to be shown? (see jobs.h)
bool jobs_output_waiting (void) {
    return output_count > 0 || output_lost > 0;
}

// show what has been printed, and forget it (see jobs.h)
void jobs_show_output (void) {
    if (!jobs_output_waiting()) {
        return;
    }
    enum color old_fg = console_fg;
    colour(LIGHT_CYAN, console_bg);
    // take each character from the start, wrapping round at the end of the array
    bool line_ended = true;
    while (output_count > 0) {
        char c = output[output_start];
        output_start = (output_start + 1) % JOBS_OUTPUT_SIZE;
        output_count--;
        console_putchar_raw(c);
        line_ended = c == '\n';
    }
    // a program part way through a line (write() without a newline): finish the line here, so
    // the prompt starts on a line of its own, and its next character starts a new marked line
    if (!line_ended) {
        console_putchar_raw('\n');
        for (int i = 0; i < JOBS_MAX; i++) {
            jobs[i].line_start = true;
        }
    }
    if (output_lost > 0) {
        prtf("(%u characters printed in the background were lost: too much at once)\n",
             (unsigned int)output_lost);
        output_lost = 0;
    }
    console_update_cursor();
    colour(old_fg, console_bg);
}

// wait for room before printing (see jobs.h)
void jobs_wait_for_room (uint32_t bytes) {
    // a single print longer than the whole buffer could never fit: what doesn't is counted lost
    if (bytes > JOBS_OUTPUT_SIZE) {
        return;
    }
    // the terminal makes room as it shows what is waiting. Asked to stop, it stops waiting
    while (JOBS_OUTPUT_SIZE - output_count < bytes && !thread_stopping()) {
        thread_sleep(20);
    }
}


// ---- starting and stopping ----------------------------------------------------------------------

// A background program's thread (see "a job" at the top): its own folder and output, the
// program, then a last line saying it ended
static void run_job (void* arg) {
    job_t* job = arg;
    this_job = job;
    scpy(cd, job->folder, sizeof(cd));
    console_divert = keep_output;

    int result = aer_run_file_background(job->path, job->args);
    unsigned int seconds = (unsigned int)((clkuptms() - job->started) / 1000);
    if (result == 0) {
        prtf("finished, after %u s\n", seconds);
    } else {
        prtf("ended, after %u s\n", seconds);
    }

    console_divert = NULL;
    this_job = NULL;
    job->used = false;
}

// start a program in the background (see jobs.h)
int jobs_start (const char* path, const char* args) {
    job_t* job = NULL;
    for (int i = 0; i < JOBS_MAX && job == NULL; i++) {
        if (!jobs[i].used) {
            job = &jobs[i];
        }
    }
    if (job == NULL) {
        return -1;
    }
    scpy(job->path, path, sizeof(job->path));
    scpy(job->args, args != NULL ? args : "", sizeof(job->args));
    scpy(job->folder, cd, sizeof(job->folder));

    // its name: the last part of the path, without ".aer" ("games/snake.aer" is "snake")
    const char* last = strrchr(path, '/');
    scpy(job->name, last != NULL ? last + 1 : path, sizeof(job->name));
    size_t length = slen(job->name);
    if (length > 4 && aer_is_program_name(job->name, length)) {
        job->name[length - 4] = '\0';
    }

    job->started = clkuptms();
    job->line_start = true;
    job->used = true;
    job->thread = thread_create(job->name, run_job, job, JOB_STACK_SIZE);
    if (job->thread < 0) {
        job->used = false;
        return -1;
    }
    return job->thread;
}

// ask one to stop (see jobs.h)
bool jobs_stop (int id) {
    for (int i = 0; i < JOBS_MAX; i++) {
        if (jobs[i].used && jobs[i].thread == id) {
            return thread_ask_to_stop(id);
        }
    }
    return false;
}

// stop them all and wait (see jobs.h)
void jobs_stop_all (void) {
    // ask them all first, so they stop side by side, then wait for each
    int ids[JOBS_MAX];
    int count = 0;
    for (int i = 0; i < JOBS_MAX; i++) {
        if (jobs[i].used) {
            thread_ask_to_stop(jobs[i].thread);
            ids[count++] = jobs[i].thread;
        }
    }
    for (int i = 0; i < count; i++) {
        thread_join(ids[i]);
    }
    // what they printed was for the person logging out, not whoever logs in next
    output_start = 0;
    output_count = 0;
    output_lost = 0;
}

// how many are running (see jobs.h)
int jobs_running (void) {
    int count = 0;
    for (int i = 0; i < JOBS_MAX; i++) {
        if (jobs[i].used) {
            count++;
        }
    }
    return count;
}

// BG on its own (see jobs.h)
void jobs_list (void) {
    if (jobs_running() == 0) {
        prtf("Nothing is running in the background. BG <program> starts something.\n");
        return;
    }
    prtf("ID  PROGRAM                          RUNNING FOR\n");
    for (int i = 0; i < JOBS_MAX; i++) {
        if (!jobs[i].used) {
            continue;
        }
        char what[FS_PATH_MAX + ARGS_MAX];
        sfmt(what, sizeof(what), "%s %s", jobs[i].path, jobs[i].args);
        prtf("%-3d %-32s %u s\n", jobs[i].thread, what, (unsigned int)((clkuptms() - jobs[i].started) / 1000));
    }
    prtf("STOP <id> stops one.\n");
}

// each thread's own job, folder and output (see jobs.h)
void jobs_keep_per_thread (void) {
    thread_keep(&this_job, sizeof(this_job));
    thread_keep(&console_divert, sizeof(console_divert));
    thread_keep(cd, sizeof(cd));
}
