/*
    update.c - updating AerOS on an installed disk, and going back if the update doesn't start
    (see update.h for what is checked, what changes on the disk, and how going back works)
*/

#include "update.h"
#include "f_sys.h"
#include "grubenv.h"
#include "syslog.h"
#include "disk_cache.h"
#include "package_key.h"
#include "../lib/ed25519.h"
#include "../lib/crypto.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_stdio.h"

// The build stamp, "AEROS-BUILD:20261204183000 0.02 ALPHA", written by the Makefile when
// main.elf is linked (obj/build_stamp.c)
extern const char aeros_build_stamp[];

// GRUB's menu for an installed disk, built into AerOS (src/install/grub.cfg, see the Makefile)
extern const char _binary_install_grub_cfg_start[], _binary_install_grub_cfg_end[];

// what the word on the command line said (update.h): set by boot.c
int update_start = UPDATE_START_NORMAL;

// What every build stamp starts with (the digits come straight after it). The byte 1 in front
// is never in text, so the examples of a stamp in AerOS's own source, which is built in (/src/),
// can't be taken for the real one
static const char stamp_start[] = "\001AEROS-BUILD:";

// the name of the setting in /grubenv that GRUB reads (update.h)
#define SETTING "aeros_update"

// a sentence put together with a number in it, for update_install to give back
static char problem[160];

// this AerOS's build number (see update.h)
const char* update_build (void) {
    static char build[UPDATE_BUILD_DIGITS + 1];
    scpy(build, aeros_build_stamp + slen(stamp_start), sizeof(build));
    return build;
}

// this AerOS's version: what comes after the build number and its space (see update.h)
const char* update_version (void) {
    return aeros_build_stamp + slen(stamp_start) + UPDATE_BUILD_DIGITS + 1;
}

// Find the build number in a main.elf: the 14 digits straight after stamp_start. (AerOS's own
// code has stamp_start in it too, as the text above, but with no digits after it, so
// that one is passed over.) False if it has none
static bool find_build (const uint8_t* file, uint32_t size, char out[UPDATE_BUILD_DIGITS + 1]) {
    uint32_t start_length = (uint32_t)slen(stamp_start);
    for (uint32_t at = 0; at + start_length + UPDATE_BUILD_DIGITS <= size; at++) {
        if (file[at] != stamp_start[0] || memcmp(file + at, stamp_start, start_length) != 0) {
            continue;
        }
        const uint8_t* digits = file + at + start_length;
        bool all_digits = true;
        for (int i = 0; i < UPDATE_BUILD_DIGITS; i++) {
            if (digits[i] < '0' || digits[i] > '9') {
                all_digits = false;
            }
        }
        if (all_digits) {
            memcpy(out, digits, UPDATE_BUILD_DIGITS);
            out[UPDATE_BUILD_DIGITS] = '\0';
            return true;
        }
    }
    return false;
}

// Is it a kernel GRUB can start? An ELF file ("\x7f" "ELF" at the start) with a multiboot header
// in its first 8 KB, where GRUB looks for one: the number 0x1BADB002, on a 4 byte boundary,
// then its flags and a checksum that make the three add up to 0
static bool looks_like_a_kernel (const uint8_t* file, uint32_t size) {
    if (size < 8192 || memcmp(file, "\x7f" "ELF", 4) != 0) {
        return false;
    }
    for (uint32_t at = 0; at + 12 <= 8192; at += 4) {
        const uint32_t* header = (const uint32_t*)(file + at);
        if (header[0] == 0x1BADB002 && (uint32_t)(header[0] + header[1] + header[2]) == 0) {
            return true;
        }
    }
    return false;
}

// Was `file` signed with the packages' key (sys/package_key.h), the one built into AerOS?
static bool signed_by_the_makers (const uint8_t* file, uint32_t size, const char* signature_hex) {
    uint8_t signature[ED25519_SIGNATURE_SIZE];
    uint8_t key[ED25519_KEY_SIZE];
    return slen(AEROS_PACKAGE_KEY) == 2 * ED25519_KEY_SIZE &&
           slen(signature_hex) == 2 * ED25519_SIGNATURE_SIZE &&
           crypto_from_hex(signature_hex, signature, sizeof(signature)) &&
           crypto_from_hex(AEROS_PACKAGE_KEY, key, sizeof(key)) &&
           ed25519_verify(signature, file, size, key);
}

// Write a whole file in /boot/, replacing it if it is there, and read it back to be sure it is
// all there as it should be. True if it is
static bool write_and_check (const char* name, const void* data, uint32_t size) {
    int result;
    if (fs_file_exists(UPDATE_FOLDER, name)) {
        result = fs_write_bytes(UPDATE_FOLDER, name, data, size, false);
    } else {
        result = fs_create_bytes(UPDATE_FOLDER, name, data, size);
    }
    if (result != FS_OK || !disk_cache_sync()) {
        return false;
    }
    uint32_t back_size = 0;
    char* back = fs_read_bytes(UPDATE_FOLDER, name, &back_size);
    bool same = back != NULL && back_size == size && memcmp(back, data, size) == 0;
    free(back);
    return same;
}

// Put this AerOS's own GRUB menu on the disk, if the disk's is different: it knows about
// old.elf, and anything else GRUB's menu has learned since the disk was installed
static void refresh_grub_menu (void) {
    const char* folder = "/boot/grub/";
    uint32_t length = (uint32_t)(_binary_install_grub_cfg_end - _binary_install_grub_cfg_start);
    uint32_t size = 0;
    char* now = fs_read_bytes(folder, "grub.cfg", &size);
    bool same = now != NULL && size == length && memcmp(now, _binary_install_grub_cfg_start, length) == 0;
    free(now);
    if (!same && fs_dir_exists(folder)) {
        if (fs_file_exists(folder, "grub.cfg")) {
            fs_write_bytes(folder, "grub.cfg", _binary_install_grub_cfg_start, length, false);
        } else {
            fs_create_bytes(folder, "grub.cfg", _binary_install_grub_cfg_start, length);
        }
        disk_cache_sync();
    }
}

// can this AerOS be updated here? (see update.h)
const char* update_unavailable (void) {
    if (fs_mode != FS_MODE_DISK) {
        return "AerOS isn't using a disk: only AerOS installed on a disk (INSTALL) can be updated.";
    }
    if (!fs_file_exists(UPDATE_FOLDER, UPDATE_KERNEL)) {
        return "This AEROS disk has no /boot/main.elf: it wasn't made by INSTALL, so AerOS doesn't "
               "start from it. Start AerOS from a newer CD instead.";
    }
    return NULL;
}

// check a new main.elf, changing nothing (see update.h)
const char* update_check (const uint8_t* kernel, uint32_t size, const char* signature, char* new_build, char* old_build) {
    scpy(new_build, "?", UPDATE_BUILD_DIGITS + 1);
    scpy(old_build, "?", UPDATE_BUILD_DIGITS + 1);
    const char* unavailable = update_unavailable();
    if (unavailable != NULL) {
        return unavailable;
    }
    if (size > UPDATE_LARGEST || !looks_like_a_kernel(kernel, size)) {
        return "That isn't an AerOS kernel (GRUB couldn't start it): nothing was changed.";
    }
    if (!signed_by_the_makers(kernel, size, signature)) {
        return "That AerOS isn't signed by AerOS's makers, so it won't be used: nothing was changed.";
    }
    if (!find_build(kernel, size, new_build)) {
        return "That AerOS has no build number, so it can't be told apart: nothing was changed.";
    }
    uint32_t old_size = 0;
    char* old = fs_read_bytes(UPDATE_FOLDER, UPDATE_KERNEL, &old_size);
    if (old == NULL) {
        return "Couldn't read /boot/main.elf: nothing was changed.";
    }
    bool has_build = find_build((const uint8_t*)old, old_size, old_build);
    free(old);
    // (the digits are the same length, so comparing them as text puts them in time order)
    if (has_build && scmp(new_build, old_build) <= 0) {
        sfmt(problem, sizeof(problem), "That AerOS (build %s) isn't newer than the one on this disk "
             "(build %s): nothing was changed.", new_build, old_build);
        return problem;
    }
    return NULL;
}

// check a new main.elf and put it on the disk (see update.h)
const char* update_install (const uint8_t* kernel, uint32_t size, const char* signature) {
    // 1. the checks, before anything changes
    char new_build[UPDATE_BUILD_DIGITS + 1], old_build[UPDATE_BUILD_DIGITS + 1];
    const char* wrong = update_check(kernel, size, signature, new_build, old_build);
    if (wrong != NULL) {
        return wrong;
    }
    uint32_t old_size = 0;
    char* old = fs_read_bytes(UPDATE_FOLDER, UPDATE_KERNEL, &old_size);
    if (old == NULL) {
        return "Couldn't read /boot/main.elf: nothing was changed.";
    }

    // 2. the one there now, kept as old.elf, for GRUB to go back to
    if (!write_and_check(UPDATE_OLD, old, old_size)) {
        free(old);
        return "Couldn't keep a copy of the AerOS there now as /boot/old.elf (is the disk full?): "
               "AerOS will start as it did.";
    }
    // 3. GRUB's menu that knows about it, and the setting that tells GRUB to try the new one
    refresh_grub_menu();
    if (!grubenv_set(SETTING, "new")) {
        free(old);
        return "Couldn't write /grubenv: AerOS will start as it did.";
    }
    // 4. the new one. If it can't be written, the old one goes back, and GRUB isn't told
    if (!write_and_check(UPDATE_KERNEL, kernel, size)) {
        fs_write_bytes(UPDATE_FOLDER, UPDATE_KERNEL, old, old_size, false);
        grubenv_set(SETTING, "");
        disk_cache_sync();
        free(old);
        return "Couldn't write the new /boot/main.elf (is the disk full?): the old one is back, "
               "and AerOS will start as it did.";
    }
    free(old);
    syslog_add("boot", "update", "AerOS build %s was put on the disk, to start next time (build %s "
               "is kept as %s%s)", new_build, old_build, UPDATE_FOLDER, UPDATE_OLD);
    return NULL;
}

// finish an update, or go back from one (see update.h)
int update_after_boot (char* said, uint32_t size) {
    if (update_start == UPDATE_START_NORMAL || update_unavailable() != NULL) {
        return UPDATE_NOTHING;
    }
    // the new one has started: GRUB can stop trying it, and its menu is brought up to date
    if (update_start == UPDATE_START_TRIAL) {
        grubenv_set(SETTING, "");
        refresh_grub_menu();
        sfmt(said, size, "Updated: this is AerOS %s, build %s (the one before is kept as %s%s)",
             update_version(), update_build(), UPDATE_FOLDER, UPDATE_OLD);
        return UPDATE_WORKED;
    }

    // UPDATE_START_FALLBACK: this is old.elf, as the new one didn't start. It goes back to
    // being main.elf, and the new one is gone
    uint32_t mine_size = 0;
    char* mine = fs_read_bytes(UPDATE_FOLDER, UPDATE_OLD, &mine_size);
    uint32_t failed_size = 0;
    char* failed = fs_read_bytes(UPDATE_FOLDER, UPDATE_KERNEL, &failed_size);
    char failed_build[UPDATE_BUILD_DIGITS + 1] = "?";
    if (failed != NULL) {
        find_build((const uint8_t*)failed, failed_size, failed_build);
        free(failed);
    }
    bool back = mine != NULL && write_and_check(UPDATE_KERNEL, mine, mine_size);
    free(mine);
    if (back) {
        fs_delete_f(UPDATE_FOLDER, UPDATE_OLD);
        grubenv_set(SETTING, "");
        disk_cache_sync();
        sfmt(said, size, "The update to build %s didn't start, so AerOS went back to build %s",
             failed_build, update_build());
    } else {
        // GRUB will keep starting old.elf, which works, so nothing is lost: it is said, though
        sfmt(said, size, "The update to build %s didn't start, and %s%s couldn't be put back as %s",
             failed_build, UPDATE_FOLDER, UPDATE_OLD, UPDATE_KERNEL);
    }
    return UPDATE_WENT_BACK;
}
