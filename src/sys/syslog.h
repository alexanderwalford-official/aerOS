/*
    syslog.h - AerOS's log: what happened at each boot, kept in a database

    Everything is in one AerDB database (db/aerdb.h), /sys/logs.db, in two tables:

        boots:  boot  started              ended                clean
                41    2026-11-15 09:02:11  2026-11-15 11:40:03  1
                42    2026-11-15 11:41:30  NULL                 NULL     <- running now

        log:    boot  time                 kind     source   message
                42    2026-11-15 11:41:30  boot     disk     AEROS disk found: files are saved on it
                42    2026-11-15 11:41:31  warning  sound    No sound card found ...

    `boot` counts up from 1. A boot's `clean` is 1 once it has shut down or restarted properly
    (OFF or RBT), and 0 if AerOS found at the next boot that it never did: the power went, or it
    crashed. A log entry's `kind` is one of:

        boot       what was found and started while booting
        shutdown   OFF or RBT
        warning    something that didn't work, but AerOS carries on without it
        error      something that should have worked and didn't
        app        written by a program, with logadd(message): its source is the program's name

    Times are text, "2026-11-15 11:41:30", biggest unit first, so ORDER BY time sorts them.

    HOW IT GOES
        syslog_add      any time, even before the disk is ready: the entry waits in memory
        syslog_start    once the file system is up (init_boot): makes the tables the first
                        time, notices a boot that never ended properly, adds this boot, forgets
                        boots older than SYSLOG_BOOTS_KEPT, then writes what was waiting
        syslog_flush    before each prompt: writes anything added since
        syslog_stop     at OFF and RBT: writes the last entries, and marks this boot clean

    The database is opened, written and closed each time, never left open: an AerDB file must
    only be open once at a time, and a program reading the log opens it too. In VFS mode it is
    in memory like every other file, so it only lasts until the computer is turned off.

    WHO CAN CHANGE IT
    Only this file. Anything else that tries to change /sys/logs.db, from the terminal or a
    program, gets FS_ERR_READ_ONLY (f_sys.c), and a program isn't even asked about it (perm.c).
    Anyone can read it: "sql /sys/logs.db SELECT ...", or dbopen("/sys/logs.db", "read").
    The one way to clear it is AerLang's logclear() (lang/bi_system.c), which asks the person at
    the keyboard Y / N and their password before calling syslog_clear().

    docs/LOGS.md explains the whole thing: why it was built this way, a boot's life step by
    step, and how to add entries of your own.
*/

#ifndef SYSLOG_H
#define SYSLOG_H

#include <stdbool.h>

#define SYSLOG_FOLDER      "/sys/"
#define SYSLOG_FILE        "logs.db"
#define SYSLOG_BOOTS_KEPT  20      // the log of older boots is deleted at the next boot

// Add an entry: kind (see above), source (what it's about: "disk", "sound", a program's name),
// and the message, written like prtf ("%d file(s) added"). It is written at the next
// syslog_flush, or once syslog_start has run. Not from an interrupt handler
void syslog_add (const char* kind, const char* source, const char* format, ...)
    __attribute__((format(printf, 3, 4)));

// The file system is up: open the log, start this boot's entry, and write what was waiting.
// If the last boot didn't end properly, says so in the boot log (and the log). Call once
void syslog_start (void);

// write the entries added since the last time (does nothing if there are none)
void syslog_flush (void);

// AerOS is shutting down (restarting = false) or restarting: log it, write everything, and
// mark this boot as having ended properly
void syslog_stop (bool restarting);

// Clear the log: every entry, and every boot before this one (this boot stays, so the numbers
// carry on). Then one warning entry records who cleared it and with what ("aerlog", or
// "the prompt"). Doesn't ask anyone: lang/bi_system.c's logclear() asks first. False if the
// log couldn't be opened or changed
bool syslog_clear (const char* user, const char* with);

// this boot's number, counting from 1 (0 until syslog_start has run)
int syslog_boot (void);

#endif /* SYSLOG_H */
