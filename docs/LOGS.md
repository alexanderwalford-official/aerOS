# AerOS's log

AerOS keeps a log of what happens to it: every boot, what it found while booting, warnings and
errors, every OFF and RBT, and messages from programs. It lives in one AerDB database,
`/sys/logs.db`, so it is read with SQL like any other database.

This guide is for anyone reading or changing the code. The user's side is in the manual inside
AerOS (`doc logs`, from `src/disk/docs/logs.md`).

> **Not to be confused with** the AerDB *journal* in `docs/plans/DATABASE.md`'s "Later" list.
> That is a crash-safety file for the database itself (old pages kept while a statement is
> written), which nobody reads. This log is a record of what happened, which people do read.


## Why it exists

1. **To know what happened.** The boot log flashes past in two seconds. The log keeps it, for
   the last 20 boots, so "did the sound card work last Tuesday?" has an answer.
2. **To notice a crash.** A boot that never reached OFF or RBT ended badly: the power went, or
   AerOS stopped. The next boot finds it, marks it, and says so. Most hobby operating systems
   (and many big ones) can't tell you this.
3. **To be trusted.** Only AerOS can write to it. A program, or a person at the terminal, can
   read it but not change it, so what it says really happened.
4. **To show the database working.** It is a real, everyday use of AerDB: a program that wants
   a log viewer needs nothing new, just `dbopen` and `dbquery`.


## The pieces

```
  WHO ADDS ENTRIES                         THE LOG                          WHERE IT IS KEPT
  ----------------                         -------                          ----------------
  sys/boot.c       boot_report() --.
    each [ OK ] / [WARN] / [FAIL]  |
    line, the disk mode, the apps  |
                                   |       sys/syslog.c
  sys/power.c      sys_reboot(),   +---->  syslog_add()   -> `waiting`     (in memory, up to 48)
                   sys_shutdown()  |       syslog_start() -.
                                   |       syslog_flush() -+-> aerdb_exec(SQL) -> db/ (AerDB)
  lang/bi_system.c logadd() -------'       syslog_stop()  -'                       |
                                                                                   v
  sys/terminal/t_int.c: before each prompt, syslog_flush()            sys/f_sys.c: fs_write_at()
                                                                      read_only_file(): only
                                                                      while fs_log_unlocked
                                                                                   |
                                                                                   v
  WHO READS IT                                                            /sys/logs.db
  ------------                                                                     ^
  the SQL command (cmd_database.c)  -- aerdb_open_read() ------------------------->|
  a program: dbopen(path, "read") (bi_database.c, perm.c: no question asked) ----->|
```

| File | Its part |
| --- | --- |
| `sys/syslog.h`, `sys/syslog.c` | the log itself: entries waiting in memory, and the SQL that writes them |
| `sys/boot.c` | `boot_report()` prints each boot line and logs it; calls `syslog_start()` |
| `sys/power.c` | `syslog_stop()` at the start of `sys_reboot()` and `sys_shutdown()` |
| `sys/terminal/t_int.c` | `syslog_flush()` before each prompt |
| `lang/bi_system.c` | `logadd(message)` and `logclear()` for AerLang programs |
| `sys/f_sys.c` | `read_only_file()`: nothing but syslog.c may change `/sys/logs.db` |
| `sys/perm.c` | a program asking to change the log is refused without a question; `perm_confirm()` asks the Y / N for `logclear()` |
| `db/file.c`, `db/run.c` | `aerdb_open_read()`: AerDB's read-only mode |
| `lang/bi_database.c` | `dbopen(path, "read")` |
| `sys/terminal/cmd_database.c` | the SQL command opens the log read-only |
| `sys/explain.c` | what `why` says after "it can be read but not changed" |


## What is in it

Two tables, made by `syslog_start()` the first time with `CREATE TABLE IF NOT EXISTS`:

```
boots:  boot  started              ended                clean
        41    2026-11-15 09:02:11  2026-11-15 11:40:03  1        <- ended with OFF or RBT
        42    2026-11-15 11:41:30  NULL                 0        <- never ended: found at boot 43
        43    2026-11-15 12:05:09  NULL                 NULL     <- running now

log:    boot  time                 kind     source   message
        43    2026-11-15 12:05:09  boot     aeros    AerOS 0.01 ALPHA started
        43    2026-11-15 12:05:09  warning  sound    No sound card found (AerOS supports AC'97), ...
        43    2026-11-15 12:05:10  warning  power    boot 42 (started 2026-11-15 11:41:30) didn't ...
        43    2026-11-15 12:09:44  app      snake    new high score
```

| Column | Holds |
| --- | --- |
| `boot` | counts up from 1, one more than the last boot in `boots` |
| `started`, `ended`, `time` | text, `"2026-11-15 11:41:30"`, from the CMOS clock. Biggest unit first, so text order is time order, and `ORDER BY time` works |
| `clean` | NULL while running; 1 once OFF or RBT ended it; 0 if the next boot found it never ended |
| `kind` | `boot`, `warning`, `error`, `shutdown` or `app` (below) |
| `source` | what it's about: `disk`, `sound`, `network`, `power`, `log`, or a program's name |
| `message` | up to 127 characters |

| Kind | Written by | For |
| --- | --- | --- |
| `boot` | `boot_report("[ OK ]" or "[INFO]", ...)`, and the disk and apps lines | what was found and started |
| `warning` | `boot_report("[WARN]", ...)`, a boot that never ended, lost entries | something didn't work; AerOS carries on |
| `error` | `boot_report("[FAIL]", ...)`, built-in files that couldn't be written | something should have worked and didn't |
| `shutdown` | `syslog_stop()` | OFF or RBT |
| `app` | `logadd(message)` | whatever a program wants to record |


## A boot's life

```
power on
 |
 |  init_boot() (boot.c)
 |    syslog_add("boot", "aeros", "AerOS ... started")
 |    boot_report(...) for each line          the disk isn't ready: every entry WAITS in memory
 |    fs_init()                                the file system is up from here
 |    syslog_add("boot", "disk", ...), the apps
 |    syslog_start()  ---------------------->  opens /sys/logs.db and, one SQL statement each:
 |                                               1. CREATE TABLE IF NOT EXISTS boots / log
 |                                               2. SELECT boot, started FROM boots WHERE clean IS NULL ...
 |                                                  (a boot still "running" from before = it crashed)
 |                                               3. UPDATE boots SET clean = 0 WHERE clean IS NULL
 |                                               4. SELECT boot FROM boots ORDER BY boot DESC LIMIT 1
 |                                               5. INSERT INTO boots (boot, started) VALUES (?, ?)
 |                                               6. past 20 boots: DELETE the oldest (COMPACT every 10th)
 |                                               7. if step 2 found one: print [WARN], and log it
 |                                               8. INSERT INTO log ... for each waiting entry
 |                                             closes it
 |
 |  the terminal (t_int.c), round and round:
 |    syslog_flush()  ---------------------->  nothing waiting? nothing happens.
 |                                             Otherwise: open, INSERT each, close
 |    the prompt, a command or a program       (logadd() only adds to `waiting`)
 |
 |  OFF or RBT (power.c)
 |    syslog_stop()   ---------------------->  syslog_add("shutdown", ...), open, INSERT each,
 |                                             UPDATE boots SET ended = ?, clean = 1 WHERE boot = ?,
 |                                             close
 v
power off
```

If the power goes instead, step 2 at the next boot finds this boot's `clean` still NULL.


## How it works, and why it was built this way

### Entries wait in memory, and are written in batches

`syslog_add()` never touches the disk. It stamps the entry with the time and puts it in
`waiting`, an array of 48. Three things write them out: `syslog_start()`, `syslog_flush()`
before each prompt, and `syslog_stop()`.

- **The first entries happen before there is a disk.** The boot log's lines come before
  `fs_init()`, so they have to wait somewhere.
- **Writing an entry costs more than it looks.** An AerDB INSERT rewrites the table's last page
  and page 1, 8 KB, for a 60 byte entry. Collecting them means one open and close for many.
- **Never in the middle of something.** Writing between commands means a command or program is
  never slowed down or interrupted by the log.

If more than 48 pile up before a flush, the extra ones are counted rather than kept, and the
next write adds a warning saying how many were lost (`waiting` has one more place, kept for
that note).

### The database is opened, written and closed each time

AerDB keeps a copy of page 1 (the header: which pages each table has) in memory while a
database is open. If two pieces of code had the same file open at once, each would write its
own copy of page 1 back and undo the other's changes. So the log is never left open: every
write is open, write, close. A program reading the log at the same time is safe too, because
the log only writes at the prompt, never while a program is running.

### Only AerOS can change it

Three layers, from the bottom up:

1. **The file system** (`read_only_file()` in `f_sys.c`). Every function that changes a file
   (create, write, write in place, truncate, delete) asks it first. `/sys/logs.db` gets
   `FS_ERR_READ_ONLY` unless `fs_log_unlocked` is true, and only syslog.c sets that, in
   `open_log()`, setting it back in `close_log()`. This is the layer that really protects it:
   DEL, WR, the SQL command and AerLang's file functions all end up here.
2. **Permissions** (`perm_allows()` in `perm.c`). A program asking to change the log is told
   no without a question being asked, the same way `/acc/` is never allowed. There is no
   point asking "may this program change the log?" when the answer can only be no.
3. **Read-only opening** (`aerdb_open_read()`). The SQL command and `dbopen(path, "read")` open
   the log only to read, so a change is turned away by AerDB before it starts, with a clear
   message ("logs.db was opened only to read"), rather than failing halfway through a write.

The file-system rule is the same idea as `/src/` being read-only, and uses the same error, so
`why` explains both in one place (`PROBLEM_READ_ONLY` in `explain.c`).

### Clearing it: the one way in, and it asks

A log viewer needs a "clear the log" option, but letting programs change the log would undo
everything above. So clearing is a single built-in, `logclear()` (`bi_logclear` in
`lang/bi_system.c`), and **AerOS** decides, not the program:

```
logclear()
  |  the program still has the log open (database_is_open)?  -> stops with an error
  |  graphics on?                                             -> stops with an error
  |
  |  perm_confirm("aerlog.aer wants to clear AerOS's log ...")    Y / N, asked every time
  |     no -> false                                               ("A" isn't an answer here)
  |
  |  accounts in use (a disk)? "Your password (alex): "           auth_check(), then wiped
  |     wrong -> a warning entry saying so, and false
  |
  v  syslog_clear(user, program)
       DELETE FROM log
       DELETE FROM boots WHERE boot != (this boot)      this boot stays: numbering carries on
       COMPACT                                          cleared means gone from the file
       a warning entry: "the log was cleared by alex, with aerlog.aer"
  -> true
```

Why each step:

- **The question comes from AerOS, not the program.** `perm_confirm()` (perm.c) uses the
  same yellow `[ ? ]` line as a permission question, so a program can't fake or skip it. It
  has no "always": it's for things that should never be agreed to in advance.
- **The password** is the logged in user's, checked the same way `PW` checks it. Someone
  passing a logged-in computer can't wipe the record of what they did. With no disk there are
  no accounts and the log is only in memory, so Y / N is enough.
- **A wrong password is logged.** Someone trying to clear the log is exactly what the log is
  for.
- **The log must be closed first.** COMPACT moves pages around. A copy the program opened
  before would still have the old page numbers in its copy of page 1 and read the wrong rows,
  so `logclear()` refuses rather than letting that happen.
- **Text screen only.** The password is typed on the text screen; asking for it over a
  program's graphics isn't supported (yet).

### AerDB's read-only mode

`aerdb_open_read()` opens an existing database (it never makes one) and sets `read_only` in
`aerdb_t`. `aerdb_exec()` then refuses any statement but SELECT with `AERDB_ERR_READ_ONLY`,
and `page_write()` refuses too, as a second line of defence. It's general: any database can be
opened this way, which is what `dbopen(path, "read")` gives programs. Reading only needs
permission to read, and reading `/sys/` never asks.

### Crash detection is just SQL

There's no special flag file: the `clean` column does it. A boot starts with `clean` NULL,
and only `syslog_stop()` sets it to 1. So at the next boot, any boot whose `clean` is still
NULL didn't get there. `syslog_start()` reports the newest such boot, then sets them all to 0
so they are only reported once.

### Keeping it small

Only the last `SYSLOG_BOOTS_KEPT` (20) boots are kept: at each boot past the 20th, the rows of
older boots are deleted. AerDB's DELETE only marks rows as deleted, so every 10th boot also
runs COMPACT to give the space back. Deleting by boot rather than by size means no
`count(*)`, which AerDB doesn't have yet.


## Using it

### Reading it

```
sql /sys/logs.db SELECT * FROM boots
sql /sys/logs.db SELECT boot, started FROM boots WHERE clean = 0
sql /sys/logs.db SELECT time, source, message FROM log WHERE kind = 'warning' ORDER BY time
```

From AerLang:

```
let db = dbopen("/sys/logs.db", "read")           # no question: /sys/ is the system's own
let this_boot = dbquery(db, "SELECT boot FROM boots ORDER BY boot DESC LIMIT 1")[0][0]
for row in dbquery(db, "SELECT time, kind, message FROM log WHERE boot = ? ORDER BY time", [this_boot]) do
    print(row[0] + "  " + row[1] + "  " + row[2])
end
dbclose(db)
```

Open, query, close, rather than keeping it open for the program's whole run.

### Adding to it from C

```c
#include "syslog.h"     // or "../sys/syslog.h", from where you are

syslog_add("warning", "disk", "the disk is %d%% full", percent);
```

- Pick the kind from the table above, and a short source (the driver or part of AerOS).
- Write the message like `prtf`. It's cut at 127 characters, so put the important part first.
- Any time from boot onwards, but **not from an interrupt handler**: it reads the CMOS clock,
  and a handler that interrupts another `syslog_add()` could overwrite its entry.
- For a boot line, use `boot_report()` in boot.c instead: it prints and logs in one go.

### Adding to it from AerLang

```
logadd("new high score: " + str(score))
```

The kind is `app` and the source is the program's name without `.aer` (`prompt` for code
typed at the prompt). It's written when the program ends, so the program can't read back its
own entries.

### Clearing it from AerLang

```
dbclose(db)                       # if the program has the log open, close it first
if logclear() then
    print("The log has been cleared.")
else
    print("The log was left as it is.")
end
```


## Limits, and things to know

| Limit | Why |
| --- | --- |
| 48 entries can wait between writes | a fixed array: no memory to manage. Extra ones are counted and reported |
| the last 20 boots | `SYSLOG_BOOTS_KEPT` in syslog.h |
| times are whatever the CMOS clock says | AerOS has no time zones; QEMU's clock is UTC unless told otherwise |
| no disk (VFS mode): the log is in memory | like every file; it's gone at power off, and every boot there is boot 1 |
| a power cut *while* the log is being written can leave it half-written | AerDB has no crash journal yet. The writes are short and only between commands, so it's unlikely, but possible |
| deleting the whole `/sys/` folder deletes the log too | emptying a folder doesn't go through the per-file rule. The log can't be made again until `/sys/` exists |


## Ideas for later

- **Turning logging off.** The viewer app's menu wants "stop logging". It could work like
  `logclear()`: a built-in that asks Y / N and the password, and a setting kept in `/sys/`
  that `syslog_add()` checks. (Clearing is done: see "Clearing it" above.)
- **Admin accounts.** Every account is equal today, so any logged in user can clear the log
  with their own password. If AerOS gets admins, `logclear()` is the place to check for one.
- **WHY after a crash.** `why` at the first prompt after a bad shutdown could explain what the
  log says happened last.
- **More sources.** Logins and failed passwords (`sys/accounts/`), AerLang errors
  (`aer_error`), disk errors from `fat.c`.
- **The AerDB crash journal** (`docs/plans/DATABASE.md`, "Later"). The log is the first
  database AerOS itself depends on, so it's the first good reason to build one.


## Tests

`tools/test/tests/test_logs.py` (see [TESTING.md](TESTING.md)):

| Test | Checks |
| --- | --- |
| `test_boots` | boot 1's row and entries; OFF ends it cleanly; a boot that just stops is reported at the next boot, and marked `clean = 0` |
| `test_only_aeros_changes_it` | `files/logs.aer` reads the log from a program without a question, can't open it to change it, and adds an entry with `logadd`; DEL, WR and the SQL command are refused, and WHY explains |
| `test_in_memory` | with no disk, the log is in memory |
| `test_clearing` | `logclear()`: N does nothing, A isn't an answer, a wrong password does nothing and is logged, it refuses while the log is open, Y and the right password clear it (leaving this boot and one entry), and the question names the program asking |
