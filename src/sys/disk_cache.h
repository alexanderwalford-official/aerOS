/*
    disk_cache.h - recently used disk sectors kept in memory, and changes written in batches

    Where it sits:

        files (f_sys.c)  ->  FAT (fat.c)  ->  the disk cache (here)  ->  the drive (ata.c)

    Reading: a sector read before is still in memory, so it comes from there instead of the
    drive. Writing: a changed sector is kept in memory too, marked "waiting", and written to the
    drive later, together with every other waiting sector, followed by one flush (the drive's
    slowest command, which waits until the data is really on the disk). Before the cache, every
    write was followed by its own flush.

    When waiting sectors are written:

        before every prompt                       sys/terminal/t_int.c
        while AerOS waits for a key               getk() in drivers/input/IO.c
        a second after the first one changed      the disk writer thread (sys/thread.h), which
                                                  looks twice a second whatever is running; and
                                                  checked on every read or write
        when half the cache is waiting            checked on every write
        right away, for the system log            sys/syslog.c: it is what explains a crash
        before shutting down or restarting        sys/power.c
        when you type SYNC                        sys/terminal/cmd_system.c

    What it costs: if the power goes, or AerOS stops without shutting down, the changes still
    waiting are lost. That is at most about a second and a half of work done by a program that
    is still running; once AerOS is back at the prompt or waiting for a key, nothing is waiting.

    See docs/DISK_CACHE.md for how it works inside.
*/

#ifndef DISK_CACHE_H
#define DISK_CACHE_H

#include <stdint.h>
#include <stdbool.h>

// how many sectors it keeps: 8192 of 512 bytes = 4 MB (fewer if there isn't the memory)
#define DISK_CACHE_SECTORS 8192

// how long a changed sector may wait before it is written, in milliseconds
#define DISK_CACHE_HOLD_MS 1000

// what DISK_CACHE_STATS and SYNC show
typedef struct {
    uint32_t sectors;   // how many it can keep (0: no cache, everything goes straight to the drive)
    uint32_t held;      // how many it is keeping now
    uint32_t waiting;   // how many of those are changed and not written yet
    uint32_t hits;      // sectors read from memory instead of the drive, since starting
    uint32_t misses;    // sectors that had to be read from the drive
    uint32_t written;   // sectors written to the drive
    uint32_t syncs;     // how many times waiting sectors were written (each with one flush)
    uint32_t errors;    // sectors (or flushes) the drive refused
} disk_cache_stats_t;

// Start caching the drive at bus / slave (fat.c, once it has mounted the AEROS volume). Anything
// waiting for another drive is written first and forgotten
void disk_cache_start (int bus, int slave);

// Read count sectors from lba into buffer (count * 512 bytes): from memory where they are kept,
// from the drive (and then kept) where they aren't. A drive the cache isn't for is read directly.
// False on a drive error
bool disk_cache_read (int bus, int slave, uint32_t lba, uint32_t count, void* buffer);

// Write count sectors from buffer to lba: they are kept, waiting, and written later (see the top
// of this file). A drive the cache isn't for is written directly. False on a drive error
bool disk_cache_write (int bus, int slave, uint32_t lba, uint32_t count, const void* buffer);

// Write every waiting sector to the drive now, then flush the drive once. Cheap when nothing is
// waiting. False if the drive refused any of it (those stay waiting, to be tried again)
bool disk_cache_sync (void);

// the numbers in disk_cache_stats_t, as they are now
void disk_cache_stats (disk_cache_stats_t* stats);

#endif /* DISK_CACHE_H */
