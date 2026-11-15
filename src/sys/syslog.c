/*
    syslog.c - AerOS's log, kept in /sys/logs.db (syslog.h says what is in it, and when)

    New entries wait in memory, in `waiting`, and are written to the database in batches:
    syslog_start, syslog_flush and syslog_stop each open the database, write, and close it
    again. open_log also unlocks the file for that time: nothing but this file may change it
    (see fs_log_unlocked in f_sys.h), and close_log locks it again.

    It uses AerDB the way any program would, with SQL, so every step below is one statement:

        a boot with no end yet          SELECT boot, started FROM boots WHERE clean IS NULL ...
        mark it as not ended properly   UPDATE boots SET clean = 0 WHERE clean IS NULL
        the next boot's number          SELECT boot FROM boots ORDER BY boot DESC LIMIT 1
        start this boot                 INSERT INTO boots (boot, started) VALUES (?, ?)
        write an entry                  INSERT INTO log VALUES (?, ?, ?, ?, ?)
        end this boot properly          UPDATE boots SET ended = ?, clean = 1 WHERE boot = ?
*/

#include "syslog.h"
#include <stdarg.h>
#include "f_sys.h"
#include "sys_clck.h"
#include "trace.h"
#include "../db/aerdb.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdio.h"

#define WAITING_MAX 48     // entries that can wait in memory to be written
#define TIME_SIZE   20     // "2026-11-15 11:41:30" and its '\0'

// one entry, waiting to be written
typedef struct {
    char time[TIME_SIZE];
    char kind[12];          // boot, shutdown, warning, error or app
    char source[20];        // "disk", "sound", "snake"...
    char message[128];
} entry_t;

// one more place than WAITING_MAX, kept for the note saying entries were lost (write_waiting)
static entry_t waiting[WAITING_MAX + 1];
static int waiting_count = 0;
static int lost_count = 0;        // entries thrown away because `waiting` was full
static bool started = false;      // has syslog_start run (and the log opened)?
static int this_boot = 0;         // this boot's number, from syslog_start
static bool logging_on = true;    // the "logging" setting, read by syslog_start (see syslog_set_logging)

// ---- small helpers ------------------------------------------------------------------------------

// the date and time now, as "2026-11-15 11:41:30"
static void time_now (char out[TIME_SIZE]) {
    rtc_time_t t;
    rtc_read(&t);
    sfmt(out, TIME_SIZE, "%04u-%02u-%02u %02u:%02u:%02u", (unsigned int)t.year, (unsigned int)t.month,
         (unsigned int)t.day, (unsigned int)t.hour, (unsigned int)t.minute, (unsigned int)t.second);
}

// an AerDB value holding a whole number, for a ? in the SQL
static db_value_t number_value (int32_t number) {
    db_value_t value = value_null();
    value.type = DB_INT;
    value.i = number;
    return value;
}

// an AerDB value holding text, for a ? in the SQL (it points at the text, which must stay)
static db_value_t text_value (const char* text) {
    db_value_t value = value_null();
    value.type = DB_TEXT;
    value.bytes = (const uint8_t*)text;
    value.length = (uint16_t)slen(text);
    return value;
}

// Open the log, unlocking it so this file can change it. NULL if it can't be opened (a disk
// problem, or a file that isn't a database is in the way)
static aerdb_t* open_log (void) {
    fs_log_unlocked = true;
    int result;
    aerdb_t* db = aerdb_open(SYSLOG_FOLDER, SYSLOG_FILE, &result);
    if (db == NULL) {
        fs_log_unlocked = false;
        TRACE("couldn't open the log, %s%s: %s", SYSLOG_FOLDER, SYSLOG_FILE, aerdb_strerror(result));
    }
    return db;
}

// close the log, and lock it again
static void close_log (aerdb_t* db) {
    aerdb_close(db);
    fs_log_unlocked = false;
}

// fill in one entry, stamped with the time now
static void fill_entry (entry_t* entry, const char* kind, const char* source, const char* message) {
    time_now(entry->time);
    scpy(entry->kind, kind, sizeof(entry->kind));
    scpy(entry->source, source, sizeof(entry->source));
    scpy(entry->message, message, sizeof(entry->message));
}

// run one statement that gives back no rows. False (with a note for SHOW) if it failed
static bool log_run (aerdb_t* db, const char* sql, const db_value_t* values, int count) {
    if (aerdb_exec(db, sql, values, count, NULL, NULL) != AERDB_OK) {
        TRACE("the log couldn't run \"%s\": %s", sql, aerdb_error(db));
        return false;
    }
    return true;
}

// what a SELECT's first row held: its first value as a number, its second as text
typedef struct {
    bool found;
    int32_t number;
    char text[TIME_SIZE];
} first_row_t;

// keep a SELECT's first row in a first_row_t (the ctx), and stop there
static bool keep_first_row (const db_value_t* values, int count, void* ctx) {
    first_row_t* row = ctx;
    row->found = true;
    if (count > 0 && values[0].type == DB_INT) {
        row->number = values[0].i;
    }
    if (count > 1 && values[1].type == DB_TEXT) {
        // AerDB text has no '\0' on the end: copy what fits, and add one
        uint16_t length = values[1].length;
        if (length > TIME_SIZE - 1) {
            length = TIME_SIZE - 1;
        }
        memcpy(row->text, values[1].bytes, length);
        row->text[length] = '\0';
    }
    return true;
}

// run a SELECT and keep its first row (row->found is false if it gave back none)
static void log_first_row (aerdb_t* db, const char* sql, first_row_t* row) {
    memset(row, 0, sizeof(first_row_t));
    if (aerdb_exec(db, sql, NULL, 0, keep_first_row, row) != AERDB_OK) {
        TRACE("the log couldn't run \"%s\": %s", sql, aerdb_error(db));
    }
}

// Write every waiting entry into the log table, then forget them. If any were lost (too many
// at once), an entry says how many
static void write_waiting (aerdb_t* db) {
    if (lost_count > 0) {
        // `waiting` is full, but has one more place kept for this
        char note[64];
        sfmt(note, sizeof(note), "%d entries were lost: too many at once", lost_count);
        fill_entry(&waiting[waiting_count], "warning", "log", note);
        waiting_count++;
        lost_count = 0;
    }
    for (int i = 0; i < waiting_count; i++) {
        entry_t* entry = &waiting[i];
        db_value_t values[5] = {
            number_value(this_boot), text_value(entry->time), text_value(entry->kind),
            text_value(entry->source), text_value(entry->message)
        };
        if (!log_run(db, "INSERT INTO log VALUES (?, ?, ?, ?, ?)", values, 5)) {
            break; // the disk is full, say: no use trying the rest
        }
    }
    TRACE("wrote %d entr%s to the log, %s%s", waiting_count, waiting_count == 1 ? "y" : "ies",
          SYSLOG_FOLDER, SYSLOG_FILE);
    waiting_count = 0;
}

// ---- what the rest of AerOS calls ----------------------------------------------------------------

// add an entry, to be written at the next syslog_flush (see syslog.h)
void syslog_add (const char* kind, const char* source, const char* format, ...) {
    // Logging turned off: nothing is kept. (Before syslog_start, whether it is off isn't known
    // yet, so entries wait as usual and syslog_start forgets them if it turns out to be off)
    if (started && !logging_on) {
        return;
    }
    if (waiting_count >= WAITING_MAX) {
        lost_count++;
        return;
    }
    // the message, written like prtf
    char message[sizeof(waiting[0].message)];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);

    fill_entry(&waiting[waiting_count], kind, source, message);
    waiting_count++;
}

// open the log once the file system is up, and start this boot (see syslog.h)
void syslog_start (void) {
    if (started) {
        return;
    }
    aerdb_t* db = open_log();
    if (db == NULL) {
        return; // the entries keep waiting, and are lost when there are too many
    }

    // the tables, the first time
    log_run(db, "CREATE TABLE IF NOT EXISTS boots (boot INTEGER, started TEXT, ended TEXT, clean INTEGER)", NULL, 0);
    log_run(db, "CREATE TABLE IF NOT EXISTS log (boot INTEGER, time TEXT, kind TEXT, source TEXT, message TEXT)", NULL, 0);
    log_run(db, "CREATE TABLE IF NOT EXISTS settings (name TEXT, value INTEGER)", NULL, 0);

    // Is logging turned off? (No row means it has never been changed: on)
    first_row_t setting;
    log_first_row(db, "SELECT value FROM settings WHERE name = 'logging'", &setting);
    logging_on = !(setting.found && setting.number == 0);

    // A boot whose `clean` is still NULL never got to syslog_stop: AerOS stopped without OFF or
    // RBT (the power went, or it crashed). Mark it with clean = 0, and say so below
    first_row_t crashed;
    log_first_row(db, "SELECT boot, started FROM boots WHERE clean IS NULL ORDER BY boot DESC LIMIT 1", &crashed);
    log_run(db, "UPDATE boots SET clean = 0 WHERE clean IS NULL", NULL, 0);

    // this boot is the one after the last
    first_row_t last;
    log_first_row(db, "SELECT boot FROM boots ORDER BY boot DESC LIMIT 1", &last);
    this_boot = last.number + 1;
    char now[TIME_SIZE];
    time_now(now);
    db_value_t boot_row[2] = { number_value(this_boot), text_value(now) };
    log_run(db, "INSERT INTO boots (boot, started) VALUES (?, ?)", boot_row, 2);

    // Only the last SYSLOG_BOOTS_KEPT boots are kept. DELETE only marks rows as deleted, so
    // every 10th boot COMPACT gives their space back too
    if (this_boot > SYSLOG_BOOTS_KEPT) {
        db_value_t oldest[1] = { number_value(this_boot - SYSLOG_BOOTS_KEPT) };
        log_run(db, "DELETE FROM log WHERE boot <= ?", oldest, 1);
        log_run(db, "DELETE FROM boots WHERE boot <= ?", oldest, 1);
        if (this_boot % 10 == 0) {
            log_run(db, "COMPACT", NULL, 0);
        }
    }
    started = true;

    if (crashed.found) {
        prtf("[WARN] AerOS didn't shut down properly last time (boot %d, started %s)\n",
             (int)crashed.number, crashed.text);
        syslog_add("warning", "power", "boot %d (started %s) didn't shut down properly: the power "
                   "went, or AerOS stopped", (int)crashed.number, crashed.text);
    }
    // With logging off, the entries that waited for the log to open aren't kept either. The
    // boot itself is still in `boots`, so crashes are still noticed
    if (!logging_on) {
        TRACE("logging is turned off: %d waiting entr%s not kept", waiting_count, waiting_count == 1 ? "y" : "ies");
        waiting_count = 0;
        lost_count = 0;
    }
    write_waiting(db);
    close_log(db);
}

// write what was added since last time (see syslog.h)
void syslog_flush (void) {
    if (!started || (waiting_count == 0 && lost_count == 0)) {
        return;
    }
    aerdb_t* db = open_log();
    if (db == NULL) {
        return;
    }
    write_waiting(db);
    close_log(db);
}

// AerOS is shutting down or restarting: the last entries, and this boot ended properly
void syslog_stop (bool restarting) {
    if (!started) {
        return; // before the file system was up (the boot options menu): nothing to write to
    }
    syslog_add("shutdown", "power", restarting ? "restarting (RBT)" : "shutting down (OFF)");
    aerdb_t* db = open_log();
    if (db == NULL) {
        return;
    }
    write_waiting(db);
    char now[TIME_SIZE];
    time_now(now);
    db_value_t ended[2] = { text_value(now), number_value(this_boot) };
    log_run(db, "UPDATE boots SET ended = ?, clean = 1 WHERE boot = ?", ended, 2);
    close_log(db);
}

// Clear the log (see syslog.h). DELETE only marks rows as deleted, so COMPACT straight after
// gives their space back: cleared means gone from the file, not just hidden
bool syslog_clear (const char* user, const char* with) {
    if (!started) {
        return false;
    }
    aerdb_t* db = open_log();
    if (db == NULL) {
        return false;
    }
    db_value_t this_one[1] = { number_value(this_boot) };
    bool cleared = log_run(db, "DELETE FROM log", NULL, 0)
                   && log_run(db, "DELETE FROM boots WHERE boot != ?", this_one, 1)
                   && log_run(db, "COMPACT", NULL, 0);
    close_log(db);

    if (cleared) {
        TRACE("cleared the log: every entry, and every boot but this one (%d)", this_boot);
        syslog_add("warning", "log", "the log was cleared by %s, with %s", user, with);
        syslog_flush();
    }
    return cleared;
}

// Turn logging on or off (see syslog.h). The change is recorded either way: turning off, the
// entry is written while logging is still on; turning on, straight after
bool syslog_set_logging (bool on, const char* user, const char* with) {
    if (!started) {
        return false;
    }
    if (on == logging_on) {
        return true; // already that way
    }
    if (!on) {
        syslog_add("warning", "log", "logging was turned off by %s, with %s", user, with);
        syslog_flush();
    }

    // the setting: one row in the settings table, 1 for on and 0 for off
    aerdb_t* db = open_log();
    if (db == NULL) {
        return false;
    }
    db_value_t value[1] = { number_value(on ? 1 : 0) };
    bool saved = log_run(db, "DELETE FROM settings WHERE name = 'logging'", NULL, 0)
                 && log_run(db, "INSERT INTO settings VALUES ('logging', ?)", value, 1);
    close_log(db);
    if (!saved) {
        return false;
    }
    logging_on = on;
    TRACE("logging is now %s", on ? "on" : "off");

    if (on) {
        syslog_add("warning", "log", "logging was turned on by %s, with %s", user, with);
        syslog_flush();
    }
    return true;
}

// is logging on? (see syslog.h)
bool syslog_logging (void) {
    return logging_on;
}

// this boot's number (see syslog.h)
int syslog_boot (void) {
    return this_boot;
}
