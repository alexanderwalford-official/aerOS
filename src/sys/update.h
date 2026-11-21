/*
    update.h - updating AerOS on a disk it was installed on, and going back if the update
    doesn't start

    AerOS on an installed disk is one file, /boot/main.elf (sys/install.h). Updating it is
    putting a newer main.elf in its place, which the update app (/bin/update.aer) does with the
    osupdate() built-in. The new one comes from the package server, signed with the same key as
    the packages (sys/package_key.h). The server's index lists it on a line of its own:

        #os 20261204183000 <signature> 0.02 ALPHA

    (its build number, its signature, then its version), and serves it at /os/main.elf.
    tools/aerpkg.py publish-os puts it there.

    WHAT IS CHECKED, BEFORE ANYTHING ON THE DISK CHANGES

    - it is signed by the packages' key: checked here, with the key built into AerOS, not by the
      app, so no program can install a main.elf of its own
    - it is an AerOS kernel: a multiboot header, which GRUB needs to start it, and a build number
    - it is newer than the main.elf on the disk. Every main.elf carries the time it was built
      (UTC, 14 digits), in the text "AEROS-BUILD:20261204183000 0.02 ALPHA" (the Makefile writes
      it in, as obj/build_stamp.c). Without this check an old main.elf, signed but with a bug
      fixed since, could be sent again to take AerOS back to it

    WHAT CHANGES ON THE DISK

        /boot/old.elf          the main.elf there was, kept
        /boot/main.elf         the new one
        /boot/grub/grub.cfg    this AerOS's own (src/install/grub.cfg), if the disk's is different
        /grubenv               aeros_update=new (sys/grubenv.h)

    GOING BACK BY ITSELF, IF THE NEW ONE DOESN'T START

    At the next start GRUB (src/install/grub.cfg) sees aeros_update=new, changes it to "tried"
    itself, and starts main.elf with the word "trial". The new AerOS, once its file system is up
    (update_after_boot), takes the setting out: the update worked.
    If it never gets that far (it stops, or the computer has to be turned off and on), GRUB
    sees "tried" the next time, and starts old.elf with the word "fallback". That AerOS (the one
    from before) copies itself back to main.elf, and says the update didn't start.
*/
#ifndef UPDATE_H
#define UPDATE_H

#include <stdint.h>
#include <stdbool.h>

#define UPDATE_FOLDER        "/boot/"
#define UPDATE_KERNEL        "main.elf"
#define UPDATE_OLD           "old.elf"
#define UPDATE_BUILD_DIGITS  14            // "20261204183000": year, month, day, hour, minute, second
#define UPDATE_LARGEST       (16 * 1024 * 1024)

// what GRUB's menu on the disk said, in a word on the kernel's command line (sys/boot.c)
#define UPDATE_START_NORMAL   0
#define UPDATE_START_TRIAL    1   // "trial": the first start of a new AerOS
#define UPDATE_START_FALLBACK 2   // "fallback": old.elf, as the new one didn't start
extern int update_start;

// this AerOS's build number, "20261204183000", and its version, "0.02 ALPHA"
const char* update_build (void);
const char* update_version (void);

// NULL if this AerOS can be updated here: an AEROS disk with /boot/main.elf on it, from an
// install. Otherwise a sentence saying why not
const char* update_unavailable (void);

// Check a new main.elf (size bytes) and its signature (128 hex digits) as update_install does,
// changing nothing. NULL if it would be installed, or a sentence saying why not. new_build and
// old_build (UPDATE_BUILD_DIGITS + 1 each) get its build number and the disk's ("?" if unknown)
const char* update_check (const uint8_t* kernel, uint32_t size, const char* signature, char* new_build,
                          char* old_build);

// Check the new main.elf (size bytes) and its signature (128 hex digits), and put it on the disk
// to start next time (see the top of this file). NULL if it worked, or a sentence saying why it
// didn't: then nothing on the disk has changed
const char* update_install (const uint8_t* kernel, uint32_t size, const char* signature);

// After the file system is up (sys/boot.c): finish an update, or go back from one that didn't
// start. Gives back what happened for the boot log, in `said`
#define UPDATE_NOTHING   0
#define UPDATE_WORKED    1
#define UPDATE_WENT_BACK 2
int update_after_boot (char* said, uint32_t size);

#endif /* UPDATE_H */
