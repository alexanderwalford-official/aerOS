/*
    perm.h - programs ask before they touch your files

    An AerLang program can read the system's own files freely, but before it reads anything
    else, or changes any file at all, AerOS asks you:

        [ ? ] viewer.aer wants to read files in /home/alex/ (cat.bmp)
              Y = yes, this time    A = always    N = no

    (in a box over the picture instead, if the program is using graphics). The answers:

        Y   allowed until the program ends
        A   allowed from now on: kept in /acc/perms. PERM lists these, PERM DEL forgets them
        N   refused until the program ends. The function fails as if the file wasn't there,
            so the program carries on (rdf gives nil, wrf gives false...)

    WHAT NEEDS ASKING

        reading in /bin/, /docs/, /src/ or /sys/     never asks: the system's own files
        reading in the program's own folder          never asks: its own files
        reading in the folder named after the        never asks: its own files too (an
          program, next to it (/apps/viewer/           installed package's pictures and
          for /apps/viewer.aer)                        the like, see docs/PACKAGES.md)
        reading a file or folder named after the     never asks: typing "read notes.txt"
          program's name when it was started           was you asking for it
        reading anything else                        asks
        changing or deleting any file                asks
        ...in a folder the program made (mkd)        never asks while it runs: nothing of
                                                       yours can be in it yet
        anything in /acc/ (the accounts)             never allowed, whatever the answer
        changing a startup.txt in a home folder      never allowed: its lines run at login as
                                                       if you typed them (sys/startup.h)
        changing /boot/ or /grubenv                  never allowed: they start AerOS (only
                                                       osupdate() changes them, sys/update.h)

    A permission is for one folder (not the folders inside it), so a program that reads ten
    pictures from the same folder asks once. Being allowed to change files there includes
    reading them.

    The terminal's own commands, and code typed at its prompt, never ask: that is you. (So
    AerLang's cmd(), which runs a terminal command, only works at the prompt.)

    The checks are made by AerLang's file functions (lang/bi_files.c, through file_arg and
    allowed_path in lang/bi_args.c), which call
    perm_allows() before they do anything.
*/
#ifndef PERM_H
#define PERM_H

#include <stdbool.h>

// what a program wants to do with a file
typedef enum {
    PERM_READ,      // read it (or list a folder, or see whether a file is there)
    PERM_CHANGE,    // make it, change it or delete it (reading it too)
} perm_kind_t;

// a program is starting, or has ended (lang/aer_interp.c). folder and name say where the
// program's own file is ("/bin/", "viewer.aer"), args is what was typed after its name.
// perm_program_started is false if there isn't the memory, and then the program mustn't run
bool perm_program_started (const char* folder, const char* name, const char* args);
void perm_program_ended (void);

// Give each thread its own running program for these rules (thread_keep, sys/thread.h), so a
// program in the background has its own rules and answers, apart from the one in front.
// kernel.c calls it once, at boot
void perm_keep_per_thread (void);

// the running program's file name ("viewer.aer"), or "" when no program is running
const char* perm_program_name (void);

// Ask the person at the keyboard a yes / no question, in the same yellow "[ ? ]" style (or box
// over graphics) as a permission question. Unlike those there's no "always": it is asked every
// time, for things that should never be agreed to in advance (logclear() in lang/bi_system.c).
// True only for Y; N, Esc, Ctrl+C or no keyboard are no
bool perm_confirm (const char* question);

// May the running program read (PERM_READ) or change (PERM_CHANGE) the file `name` in
// `folder`? Asks if it needs to (see above). name is "" when it is about the folder itself
// (listing it). Always true when no program is running
bool perm_allows (const char* folder, const char* name, perm_kind_t kind);

// The running program has just made this folder (AerLang's mkd()): it may change files in it
// without asking, until it ends. Does nothing at the terminal, which may anyway
void perm_made_folder (const char* folder);

// the "always" answers the logged in user has given (PERM lists them): fn is called for each,
// numbered from 1. Returns how many there are
typedef void (*perm_each_fn)(int number, const char* program, perm_kind_t kind, const char* folder, void* ctx);
int perm_each (perm_each_fn fn, void* ctx);

// forget one of those answers (its number from perm_each), or all of them (number 0).
// Returns how many were forgotten
int perm_forget (int number);

#endif /* PERM_H */
