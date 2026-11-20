/*
    startup.h - your own commands, run each time you log in

    Each user can have a file of commands in their home folder, /home/<name>/startup.txt. After
    logging in, before the first prompt, AerOS runs it a line at a time, exactly as if you had
    typed each line at the prompt: so a command (fw web packages.intercon.network 80), an app
    (desktop), a program in the background (bg webserve 8080 /docs/) or a line of AerLang
    (print("Welcome back")) all work. Blank lines, and lines starting with #, are skipped.

        # startup.txt
        bg webserve 8080 /docs/
        desktop

    Edit it with EDT startup.txt (at the prompt, in your home folder). A new account gets one
    with an example in it, all of it # comments, so nothing runs until you take a # off.

    THREE THINGS IT CAN'T DO

    - Restart, shut down or log out (RBT, OFF, OUT): each would come straight back to the file
      on the next login, round and round for ever. Those lines are skipped, saying so.
    - Carry on after Ctrl+C: Ctrl+C stops the line running (as it stops any program) and the
      rest of the file with it, for a startup that has gone wrong.
    - Be changed by a program, whatever you have allowed it (sys/perm.h). Its lines run as you,
      at the prompt, where nothing ever asks first: a program that could write a line into it
      could do anything, the next time you log in. Only you, at the prompt, change it.

    It only runs from an AEROS disk (DISK mode): without one, there is nowhere to keep it.
*/
#ifndef STARTUP_H
#define STARTUP_H

#include <stdbool.h>

#define STARTUP_FILE      "startup.txt"   // in each user's home folder
#define STARTUP_LINES_MAX 64              // the most lines run: the rest are left, saying so

// run the logged in user's startup.txt, if they have one (see the top of this file)
void startup_run (void);

// write the example startup.txt in a new home folder ("/home/alex/"), all of it comments
void startup_make_example (const char* home);

// Is this file (its folder "/home/alex/" and name) someone's startup.txt? sys/perm.c never lets
// a program change one
bool startup_is_the_file (const char* folder, const char* name);

#endif /* STARTUP_H */
