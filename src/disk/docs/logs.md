# AerOS's log

AerOS keeps a log of what happens: each boot, what it found while booting, warnings, OFF and
RBT, and anything programs add. It is an AerDB [database](database.md), `/sys/logs.db`, so you
read it with SQL.

## What is in it

Two tables. `boots` has one row for each boot:

```
boot | started             | ended               | clean
41   | 2026-11-15 09:02:11 | 2026-11-15 11:40:03 | 1
42   | 2026-11-15 11:41:30 | NULL                | NULL
```

`clean` is 1 once a boot has ended properly, with OFF or RBT. If the computer stops any other
way (the power goes, or AerOS crashes), the next boot notices, sets `clean` to 0, and says so:

```
[WARN] AerOS didn't shut down properly last time (boot 41, started 2026-11-15 09:02:11)
```

`log` has the entries, each with the boot it belongs to:

```
boot | time                | kind    | source | message
42   | 2026-11-15 11:41:30 | boot    | disk   | AEROS disk found: files are saved on it
42   | 2026-11-15 11:41:30 | warning | sound  | No sound card found (AerOS supports AC'97)...
```

| kind | means |
| --- | --- |
| `boot` | what was found and started while booting (the `[ OK ]` lines) |
| `warning` | something that didn't work, but AerOS carries on without it (`[WARN]`) |
| `error` | something that should have worked and didn't (`[FAIL]`) |
| `shutdown` | OFF or RBT |
| `app` | written by a program with `logadd(message)` |

Times are written biggest unit first, so `ORDER BY time` puts them in order. The last 20 boots
are kept; older ones are deleted as new ones start.

## Reading it

From the terminal:

```
sql /sys/logs.db SELECT * FROM boots
sql /sys/logs.db SELECT time, source, message FROM log WHERE kind = 'warning'
sql /sys/logs.db SELECT boot FROM boots WHERE clean = 0
```

From a program, open it to read (no question is asked: `/sys/` is the system's own):

```
let db = dbopen("/sys/logs.db", "read")
for row in dbquery(db, "SELECT time, message FROM log WHERE boot = ? ORDER BY time", [42]) do
    print(row[0] + "  " + row[1])
end
dbclose(db)
```

A program can add an entry of its own with `logadd("found a new high score")`. Its source is
the program's name. It is written when the program ends.

## Who can change it

Nobody but AerOS. DEL, WR and the SQL command can't change it, and a program can only open it
to read: anything else is turned away with "it can be read but not changed". That way what the
log says can be trusted. `why` explains it.

With no disk (VFS mode) the log is kept in memory like every other file, and is gone when the
computer is turned off.

See also: [Databases](database.md), [Ask AerOS why](why.md), [Files and folders](files.md).
