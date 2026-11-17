/*
    disk_cache.c - recently used disk sectors kept in memory, and changes written in batches

    disk_cache.h says what it is for and when waiting sectors are written. This is how.

    THE SLOTS
    The cache is DISK_CACHE_SECTORS slots. Each holds one 512 byte sector of the disk: which
    sector (its LBA, its number on the disk), whether it was used lately (`recent`), and whether
    it was changed and not written to the drive yet (`waiting`). The sectors' bytes are kept
    together in one big block, `data`: slot i's are at data + i * 512.

    FINDING A SECTOR: buckets
    Looking through 8192 slots for one sector on every read would be slow, so the slots are
    sorted into buckets by their LBA's lowest bits (lba & mask, with as many buckets as slots).
    Each bucket is a chain: buckets[b] is its first slot, and each slot's `next` is the one
    after it. Neighbouring sectors fall in neighbouring buckets, so a chain is nearly always one
    slot long, and finding a sector means looking at one or two slots.

        buckets:  [0] -> slot 17 -> slot 902 -> end      sectors 8192 and 16384 (both & mask = 0)
                  [1] -> slot 3 -> end                   sector 8193
                  [2] -> end                             nothing kept

    MAKING ROOM: the clock
    When every slot is in use and another sector is wanted, one must go. A good choice is one
    that hasn't been used for a while. Remembering exactly when each was used, and searching
    for the oldest, would cost a lot, so instead a "clock hand" goes round the slots: a slot used
    since the hand last passed (`recent`) gets a second chance (the flag is cleared and the hand
    moves on), and the first slot without one is reused. Sectors in use keep getting their flag
    set again, so they stay; the ones nobody wants any more are the ones the hand stops at.
    A waiting slot can't just be reused, as its change would be lost: everything waiting is
    written first (disk_cache_sync), and then it can.

    WRITING: waiting, then all at once
    A write only changes the slot and marks it waiting. disk_cache_sync writes every waiting
    slot to the drive, then asks the drive to flush once. The sectors go one at a time, each
    with its own command: putting neighbours together into bigger writes would save a little,
    but the flush is what costs, and there is now one per sync instead of one per write.

    THREADS
    Nothing here runs from an interrupt, and threads only switch at safe points (sys/thread.h),
    never in the middle of a function here. So the disk writer thread and whoever is reading or
    writing never find the slots half changed, and no lock is needed.
*/

#include "disk_cache.h"
#include "../drivers/storage/ata.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_string.h"
#include "sys_clck.h"
#include "thread.h"
#include "trace.h"

// "nothing": the end of a bucket's chain, a slot with no sector in it, or no slot found
#define NONE 0xFFFFFFFF

// the fewest slots worth having: if not even this much memory is free, there is no cache
#define FEWEST_SECTORS 256

// one slot: one sector of the disk kept in memory (its bytes are in cache.data)
typedef struct {
    uint32_t lba;     // which sector of the disk it holds, or NONE when it is empty
    uint32_t next;    // the next slot in the same bucket, or NONE at the end of the chain
    bool recent;      // used since the clock hand last passed it
    bool waiting;     // changed here, and not written to the drive yet
} slot_t;

static struct {
    bool on;                  // is there a cache? (a drive started, and memory for it)
    int bus, slave;           // the drive it is for
    uint32_t count;           // how many slots (a power of two)
    uint32_t mask;            // count - 1: lba & mask is a sector's bucket
    slot_t* slots;
    uint32_t* buckets;        // each bucket's first slot, or NONE
    uint8_t* data;            // the sectors' bytes, 512 for each slot
    uint32_t hand;            // where the clock hand is (see take_slot)
    uint32_t waiting_since;   // clkuptms() when the first waiting slot started waiting
    disk_cache_stats_t stats; // what SYNC shows (held and waiting are kept up to date here too)
} cache;


// ---- finding, keeping and dropping sectors -----------------------------------------------------

// where slot s's 512 bytes are
static uint8_t* slot_data (uint32_t s) {
    return cache.data + s * SECTOR_SIZE;
}

// the slot holding sector lba, or NONE if it isn't kept
static uint32_t find_slot (uint32_t lba) {
    for (uint32_t s = cache.buckets[lba & cache.mask]; s != NONE; s = cache.slots[s].next) {
        if (cache.slots[s].lba == lba) {
            return s;
        }
    }
    return NONE;
}

// take slot s out of its bucket's chain, and empty it
static void drop_slot (uint32_t s) {
    uint32_t* link = &cache.buckets[cache.slots[s].lba & cache.mask];
    // follow the chain to the link that points at s, and point it past s instead
    while (*link != s) {
        link = &cache.slots[*link].next;
    }
    *link = cache.slots[s].next;
    cache.slots[s].lba = NONE;
    cache.stats.held--;
}

// An empty slot to use, making one if every slot is in use (the clock, see the top of this
// file). NONE only if every slot is waiting and the drive won't take them
static uint32_t take_slot (void) {
    // twice round is always enough: the first time round clears every recent flag
    for (uint32_t tries = 0; tries < 2 * cache.count + 1; tries++) {
        uint32_t s = cache.hand;
        cache.hand = (cache.hand + 1) & cache.mask;
        slot_t* slot = &cache.slots[s];
        if (slot->lba == NONE) {
            return s;
        }
        if (slot->recent) {
            slot->recent = false; // its second chance
            continue;
        }
        if (slot->waiting) {
            // its change must reach the disk before the slot can be reused
            disk_cache_sync();
            if (slot->waiting) {
                continue; // the drive didn't take it: leave it, and try the next slot
            }
        }
        drop_slot(s);
        return s;
    }
    return NONE;
}

// Keep a copy of sector lba's 512 bytes from `from`, in its slot if it has one or a new one if
// not. changed: it is a write, so it now waits to be written. False if there was no room
static bool keep_sector (uint32_t lba, const uint8_t* from, bool changed) {
    uint32_t s = find_slot(lba);
    if (s == NONE) {
        s = take_slot();
        if (s == NONE) {
            return false;
        }
        // put the new slot at the front of its bucket's chain
        uint32_t bucket = lba & cache.mask;
        cache.slots[s].lba = lba;
        cache.slots[s].next = cache.buckets[bucket];
        cache.slots[s].waiting = false;
        cache.buckets[bucket] = s;
        cache.stats.held++;
    }
    memcpy(slot_data(s), from, SECTOR_SIZE);
    cache.slots[s].recent = true;
    if (changed && !cache.slots[s].waiting) {
        cache.slots[s].waiting = true;
        if (cache.stats.waiting == 0) {
            cache.waiting_since = clkuptms();
        }
        cache.stats.waiting++;
    }
    return true;
}

// write the waiting sectors if they have waited long enough, or if half the cache is waiting
static void sync_if_due (void) {
    if (cache.stats.waiting == 0) {
        return;
    }
    bool long_enough = clkuptms() - cache.waiting_since >= DISK_CACHE_HOLD_MS;
    bool too_many = cache.stats.waiting >= cache.count / 2;
    if (long_enough || too_many) {
        disk_cache_sync();
    }
}


// The disk writer thread (sys/thread.h): twice a second, it writes whatever has waited
// DISK_CACHE_HOLD_MS. It runs at safe points of the other threads (between two statements of a
// busy AerLang program, say), never in the middle of a read or write here, so the cache is
// always in one piece when it looks at it
static void disk_writer (void* arg) {
    (void)arg;
    for (;;) {
        thread_sleep(DISK_CACHE_HOLD_MS / 2);
        if (cache.on) {
            sync_if_due();
        }
    }
}


// ---- what the rest of AerOS calls (see disk_cache.h) --------------------------------------------

// start caching a drive (see disk_cache.h)
void disk_cache_start (int bus, int slave) {
    // anything waiting for a drive used before goes to it first
    disk_cache_sync();

    // the memory, the first time: as many slots as there is room for, down to FEWEST_SECTORS
    if (cache.slots == NULL) {
        for (uint32_t count = DISK_CACHE_SECTORS; count >= FEWEST_SECTORS; count /= 2) {
            cache.data = malloc(count * SECTOR_SIZE);
            cache.slots = malloc(count * sizeof(slot_t));
            cache.buckets = malloc(count * sizeof(uint32_t));
            if (cache.data != NULL && cache.slots != NULL && cache.buckets != NULL) {
                cache.count = count;
                break;
            }
            // not enough: give back whatever was found, and try half as many
            free(cache.data);
            free(cache.slots);
            free(cache.buckets);
            cache.data = NULL;
            cache.slots = NULL;
            cache.buckets = NULL;
        }
        if (cache.slots == NULL) {
            TRACE("not enough memory for a disk cache: the drive is used directly");
            return;
        }
        // and the thread that writes waiting changes while programs run. (Without it, they are
        // still written on the next read or write after a second, and at every prompt.) Not if
        // the timer isn't ticking: its sleeps would then be busy waits, half a second each,
        // with nothing else able to run
        if (clck_ready) {
            thread_create("disk writer", disk_writer, NULL, THREAD_STACK_SIZE);
        }
    }

    // every slot empty, every bucket's chain empty
    for (uint32_t i = 0; i < cache.count; i++) {
        cache.slots[i].lba = NONE;
        cache.slots[i].waiting = false;
        cache.slots[i].recent = false;
        cache.buckets[i] = NONE;
    }
    cache.mask = cache.count - 1;
    cache.hand = 0;
    cache.bus = bus;
    cache.slave = slave;
    memset(&cache.stats, 0, sizeof(cache.stats));
    cache.stats.sectors = cache.count;
    cache.on = true;
    TRACE("keeping up to %u sectors (%u KB) of the disk in memory", (unsigned int)cache.count,
          (unsigned int)(cache.count / 2));
}

// read sectors, from memory where they are kept (see disk_cache.h)
bool disk_cache_read (int bus, int slave, uint32_t lba, uint32_t count, void* buffer) {
    if (!cache.on || bus != cache.bus || slave != cache.slave) {
        return drive_read(bus, slave, lba, count, buffer);
    }
    uint8_t* out = buffer;
    uint32_t i = 0;
    while (i < count) {
        uint32_t s = find_slot(lba + i);
        if (s != NONE) {
            // kept: copy it from memory
            memcpy(out + i * SECTOR_SIZE, slot_data(s), SECTOR_SIZE);
            cache.slots[s].recent = true;
            cache.stats.hits++;
            i++;
            continue;
        }
        // not kept: find how many in a row aren't, and read them all with one command
        uint32_t end = i + 1;
        while (end < count && find_slot(lba + end) == NONE) {
            end++;
        }
        if (!drive_read(bus, slave, lba + i, end - i, out + i * SECTOR_SIZE)) {
            return false;
        }
        // and keep them, for next time
        for (uint32_t k = i; k < end; k++) {
            keep_sector(lba + k, out + k * SECTOR_SIZE, false);
        }
        cache.stats.misses += end - i;
        i = end;
    }
    sync_if_due();
    return true;
}

// write sectors, to be written to the drive later (see disk_cache.h)
bool disk_cache_write (int bus, int slave, uint32_t lba, uint32_t count, const void* buffer) {
    if (!cache.on || bus != cache.bus || slave != cache.slave) {
        return drive_write(bus, slave, lba, count, buffer);
    }
    const uint8_t* in = buffer;
    for (uint32_t i = 0; i < count; i++) {
        if (!keep_sector(lba + i, in + i * SECTOR_SIZE, true)) {
            // no room even after writing everything waiting: this one goes straight to the drive
            if (!drive_write(bus, slave, lba + i, 1, in + i * SECTOR_SIZE)) {
                return false;
            }
        }
    }
    sync_if_due();
    return true;
}

// write every waiting sector, then flush once (see disk_cache.h)
bool disk_cache_sync (void) {
    if (!cache.on || cache.stats.waiting == 0) {
        return true;
    }
    uint32_t written = 0;
    uint32_t refused = 0;
    for (uint32_t s = 0; s < cache.count; s++) {
        if (!cache.slots[s].waiting) {
            continue;
        }
        if (drive_write_unflushed(cache.bus, cache.slave, cache.slots[s].lba, 1, slot_data(s))) {
            cache.slots[s].waiting = false;
            written++;
        } else {
            refused++; // stays waiting, to be tried again next time
        }
    }
    // one flush for all of them: the drive puts everything it was handed onto the disk
    bool flushed = drive_flush(cache.bus, cache.slave);

    cache.stats.waiting = refused;
    cache.stats.written += written;
    cache.stats.syncs++;
    cache.stats.errors += refused + (flushed ? 0 : 1);
    // whatever is still waiting (refused) waits from now, so it is tried again in a second
    cache.waiting_since = clkuptms();
    TRACE("wrote %u changed sector(s) to the disk, then flushed it once", (unsigned int)written);
    if (refused > 0 || !flushed) {
        TRACE("the drive refused %u sector(s)%s", (unsigned int)refused, flushed ? "" : ", and the flush");
    }
    return refused == 0 && flushed;
}


// the numbers, for SYNC (see disk_cache.h)
void disk_cache_stats (disk_cache_stats_t* stats) {
    *stats = cache.stats;
}
