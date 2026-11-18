/*
    install.c - putting AerOS on a disk (see install.h for what goes where, and why)

    install_to_disk() goes in four steps, each said on the screen as it is done:

        1. the boot sector and GRUB's core image, at the start of the disk (write_grub)
        2. an empty FAT32 volume labelled AEROS, in the partition (format_fat32)
        3. /boot/main.elf and /boot/grub/grub.cfg on it, with the FAT driver (write_files)
        4. main.elf read back and compared, so a disk that didn't take the write is noticed

    Everything is checked before the first sector is written: demo mode, a copy of main.elf
    from GRUB, a big enough hard disk. A failure after that leaves the disk half made, and
    installing again starts it over from the beginning.

    THE FAT32 VOLUME, sector by sector from the start of the partition

        0        the boot sector: the BIOS parameter block ("BPB"), which says how big
                 everything below is (fat.c reads it the same way, see the table there)
        1        FSInfo: a hint at how many clusters are free, so nobody has to count
        6, 7     copies of those two, in case the first ones are damaged
        32       the first FAT: one 4 byte entry per cluster, 0 = free
        32 + F   the second FAT, a copy of the first (F = sectors per FAT)
        32 + 2F  the clusters, from number 2: cluster 2 is the root folder

    https://wiki.osdev.org/FAT and Microsoft's "FAT: General Overview of On-Disk Format" have
    the rest, including where the sizes below come from.
*/

#include "install.h"
#include "boot.h"
#include "fat.h"
#include "sys_clck.h"
#include "../drivers/storage/ata.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdio.h"

// GRUB's boot sector and core image (made by the Makefile with grub-mkimage, in obj/install/),
// built in with "ld -b binary" as the files in src/disk/ are (see sys/apps.c). And grub.cfg for
// the disk, src/install/grub.cfg, which is built in already as part of AerOS's source (/src/)
extern const uint8_t _binary_obj_install_boot_img_start[], _binary_obj_install_boot_img_end[];
extern const uint8_t _binary_obj_install_core_img_start[], _binary_obj_install_core_img_end[];
extern const char _binary_install_grub_cfg_start[], _binary_install_grub_cfg_end[];

#define SECTOR        512
#define PARTITION_LBA 2048         // where the partition starts: GRUB's core image goes before it
#define LBA28_MAX     0x0FFFFFFFu  // ata.c numbers sectors with 28 bits, so it reaches 128 GB
#define RESERVED      32           // sectors before the first FAT (the boot sector, FSInfo...)
#define ZERO_CHUNK    64           // sectors of zeros written at a time when clearing (32 KB)

// little endian numbers into a sector: the low byte first
// put16 writes a 16 bit number at p
static void put16 (uint8_t* p, uint16_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

// put32 writes a 32 bit number at p
static void put32 (uint8_t* p, uint32_t value) {
    for (int i = 0; i < 4; i++) {
        p[i] = (uint8_t)(value >> (8 * i));
    }
}


// ---- which disks there are ---------------------------------------------------------------------

// what is on a disk now, for the list: "nothing" if its first sector is all zeros (a new disk),
// "AerOS" if it holds a FAT volume labelled AEROS, and "files" for anything else
static const char* disk_holds (int bus, int slave) {
    uint8_t sector[SECTOR];
    if (!drive_read(bus, slave, 0, 1, sector)) {
        return "files"; // can't tell: safer to say something is on it
    }
    bool empty = true;
    for (int i = 0; i < SECTOR; i++) {
        if (sector[i] != 0) {
            empty = false;
            break;
        }
    }
    if (empty) {
        return "nothing";
    }
    uint32_t volume_lba;
    if (drive_parse(bus, slave, &volume_lba) == 1 && drive_read(bus, slave, volume_lba, 1, sector) &&
        drive_is_fat_boot_sector(sector)) {
        // the label: at byte 71 on FAT32 (where the old 16 bit FAT size, bytes 22-23, is 0),
        // at 43 on FAT16
        const uint8_t* label = sector + ((sector[22] == 0 && sector[23] == 0) ? 71 : 43);
        if (memcmp(label, "AEROS      ", 11) == 0) {
            return "AerOS";
        }
    }
    return "files";
}

// the hard disks there are (see install.h)
int install_list_disks (install_disk_t out[INSTALL_DISKS_MAX]) {
    int count = 0;
    for (int bus = 0; bus < 2; bus++) {
        for (int slave = 0; slave < 2; slave++) {
            install_disk_t* disk = &out[count];
            uint32_t sectors = 0;
            // only ATA hard disks: a CD drive (ATAPI) is where the ISO is, not somewhere to install
            if (drive_probe_info(bus, slave, disk->model, sizeof(disk->model), &sectors) != DRIVE_ATA) {
                continue;
            }
            disk->number = bus * 2 + slave + 1;
            disk->bus = bus;
            disk->slave = slave;
            disk->megabytes = sectors / 2048; // 2048 sectors of 512 bytes are a megabyte
            disk->holds = disk_holds(bus, slave);
            count++;
        }
    }
    return count;
}


// ---- 1. GRUB -----------------------------------------------------------------------------------

// Sector 0 (GRUB's boot code, then a partition table with one partition from PARTITION_LBA for
// `partition_sectors`) and GRUB's core image from sector 1. False on a drive error
static bool write_grub (int bus, int slave, uint32_t partition_sectors) {
    uint8_t mbr[SECTOR];
    memset(mbr, 0, sizeof(mbr));
    // the first 440 bytes are GRUB's code. (Its sector 1 for the core image, at byte 0x5C, is
    // already right.) The rest, the disk's ID and the partition table, are this disk's own
    memcpy(mbr, _binary_obj_install_boot_img_start, 440);
    // GRUB's boot code starts by jumping over a check of the drive number the BIOS gives it,
    // as some BIOSes give a wrong one when starting from a USB stick or floppy. For a hard disk
    // grub-install takes the jump out (two "nop"s, 0x90), so the check is made: so does this
    mbr[0x66] = 0x90;
    mbr[0x67] = 0x90;
    // the one partition: 16 bytes at 446
    uint8_t* entry = mbr + 446;
    entry[0] = 0x80;                                    // the one the BIOS starts ("active")
    entry[1] = 0xFE; entry[2] = 0xFF; entry[3] = 0xFF;  // its start as cylinder / head / sector:
                                                        // "too far, use the sector number"
    entry[4] = 0x0C;                                    // FAT32, reached by sector number (LBA)
    entry[5] = 0xFE; entry[6] = 0xFF; entry[7] = 0xFF;  // its end, the same way
    put32(entry + 8, PARTITION_LBA);                    // its first sector
    put32(entry + 12, partition_sectors);               // and how many
    mbr[510] = 0x55;                                    // the signature every boot sector ends with
    mbr[511] = 0xAA;
    if (!drive_write_unflushed(bus, slave, 0, 1, mbr)) {
        return false;
    }

    // the core image, whole sectors of it, then its last part sector padded with zeros
    uint32_t length = (uint32_t)(_binary_obj_install_core_img_end - _binary_obj_install_core_img_start);
    uint32_t whole = length / SECTOR;
    if (whole > 0 && !drive_write_unflushed(bus, slave, 1, whole, _binary_obj_install_core_img_start)) {
        return false;
    }
    if (length % SECTOR != 0) {
        uint8_t last[SECTOR];
        memset(last, 0, sizeof(last));
        memcpy(last, _binary_obj_install_core_img_start + whole * SECTOR, length % SECTOR);
        if (!drive_write_unflushed(bus, slave, 1 + whole, 1, last)) {
            return false;
        }
    }
    return true;
}


// ---- 2. the FAT32 volume ------------------------------------------------------------------------

// sectors per cluster for a FAT32 volume of this many sectors, from Microsoft's own table:
// bigger clusters on bigger disks, so the FAT doesn't grow too big to search
static uint8_t cluster_size (uint32_t sectors) {
    uint32_t megabytes = sectors / 2048;
    if (megabytes <= 260) {
        return 1;    // 512 byte clusters
    } else if (megabytes <= 8192) {
        return 8;    // 4 KB
    } else if (megabytes <= 16384) {
        return 16;   // 8 KB
    } else if (megabytes <= 32768) {
        return 32;   // 16 KB
    }
    return 64;       // 32 KB
}

// write `count` sectors of zeros from `lba`, a chunk at a time. False on a drive error
static bool write_zeros (int bus, int slave, uint32_t lba, uint32_t count) {
    uint8_t* zeros = malloc(ZERO_CHUNK * SECTOR);
    if (zeros == NULL) {
        return false;
    }
    memset(zeros, 0, ZERO_CHUNK * SECTOR);
    bool ok = true;
    while (ok && count > 0) {
        uint32_t now = count < ZERO_CHUNK ? count : ZERO_CHUNK;
        ok = drive_write_unflushed(bus, slave, lba, now, zeros);
        lba += now;
        count -= now;
    }
    free(zeros);
    return ok;
}

// Make an empty FAT32 volume labelled AEROS in the `count` sectors from `start` (see the top of
// this file for where each part goes). False on a drive error
static bool format_fat32 (int bus, int slave, uint32_t start, uint32_t count) {
    uint8_t spc = cluster_size(count);
    // Sectors per FAT, by Microsoft's formula: every cluster needs 4 bytes in each of the two
    // FATs, so a FAT sector (128 entries) is needed for every 128 clusters. That is the same
    // as 256 * spc sectors of clusters for each 2 FAT sectors, hence the "/ 2". Rounded up
    uint32_t per_fat_sector = (256u * spc + 2) / 2;
    uint32_t fat_size = (count - RESERVED + per_fat_sector - 1) / per_fat_sector;
    uint32_t clusters = (count - RESERVED - 2 * fat_size) / spc;

    // everything from the boot sector to the end of the root folder's cluster starts as zeros:
    // free clusters in both FATs, and an empty root folder
    if (!write_zeros(bus, slave, start, RESERVED + 2 * fat_size + spc)) {
        return false;
    }

    // the boot sector (the byte offsets are the BPB table at the top of fat.c)
    uint8_t boot[SECTOR];
    memset(boot, 0, sizeof(boot));
    boot[0] = 0xEB; boot[1] = 0x58; boot[2] = 0x90; // jump over the BPB, to byte 90
    memcpy(boot + 3, "AEROS   ", 8);               // who made it
    put16(boot + 11, SECTOR);                       // bytes per sector
    boot[13] = spc;                                 // sectors per cluster
    put16(boot + 14, RESERVED);                     // reserved sectors
    boot[16] = 2;                                   // two FATs
    boot[21] = 0xF8;                                // "a hard disk"
    put16(boot + 24, 63);                           // sectors per track and heads: only for
    put16(boot + 26, 255);                          // old BIOS calls, these are the usual ones
    put32(boot + 28, start);                        // sectors before the volume
    put32(boot + 32, count);                        // sectors in it
    put32(boot + 36, fat_size);                     // sectors per FAT
    put32(boot + 44, 2);                            // the root folder's first cluster
    put16(boot + 48, 1);                            // where FSInfo is
    put16(boot + 50, 6);                            // where the copies are
    boot[64] = 0x80;                                // drive number: the first hard disk
    boot[66] = 0x29;                                // "the next three fields are filled in"
    put32(boot + 67, clkuptms() * 2654435761u);     // a serial number, from the clock
    memcpy(boot + 71, "AEROS      ", 11);           // the label AerOS looks for (fat.h)
    memcpy(boot + 82, "FAT32   ", 8);
    // nothing starts from this sector (GRUB is in sector 0 of the disk), so its code only stops
    boot[90] = 0xFA;                                // cli
    boot[91] = 0xF4;                                // hlt
    boot[92] = 0xEB; boot[93] = 0xFD;               // jmp back to the hlt
    boot[510] = 0x55;
    boot[511] = 0xAA;

    // FSInfo: every cluster is free but the root folder's, and the next free one is 3
    uint8_t info[SECTOR];
    memset(info, 0, sizeof(info));
    put32(info + 0, 0x41615252);                    // its three signatures, at the start,
    put32(info + 484, 0x61417272);                  // just before the numbers,
    put32(info + 488, clusters - 1);                // (how many clusters are free)
    put32(info + 492, 3);                           // (where to look for the next free one)
    put32(info + 508, 0xAA550000);                  // and at the end

    // the first sector of each FAT: entries 0 and 1 are kept (the first repeats the 0xF8 above),
    // and entry 2, the root folder, is its own last cluster
    uint8_t fat[SECTOR];
    memset(fat, 0, sizeof(fat));
    put32(fat + 0, 0x0FFFFFF8);
    put32(fat + 4, 0x0FFFFFFF);
    put32(fat + 8, 0x0FFFFFFF);

    // the root folder: just the label, as a folder entry too (fat.c prefers this one)
    uint8_t root[SECTOR];
    memset(root, 0, sizeof(root));
    memcpy(root, "AEROS      ", 11);
    root[11] = 0x08;                                // "this entry is the volume's label"

    uint32_t fat_lba = start + RESERVED;
    return drive_write_unflushed(bus, slave, start, 1, boot) &&
           drive_write_unflushed(bus, slave, start + 1, 1, info) &&
           drive_write_unflushed(bus, slave, start + 6, 1, boot) &&
           drive_write_unflushed(bus, slave, start + 7, 1, info) &&
           drive_write_unflushed(bus, slave, fat_lba, 1, fat) &&
           drive_write_unflushed(bus, slave, fat_lba + fat_size, 1, fat) &&
           drive_write_unflushed(bus, slave, fat_lba + 2 * fat_size, 1, root) &&
           drive_flush(bus, slave);
}


// ---- 3 and 4. the files -------------------------------------------------------------------------

// Mount the new volume, write /boot/main.elf and /boot/grub/grub.cfg, read main.elf back and
// compare it, and let the volume go again. NULL if it all worked, or what didn't
static const char* write_files (int bus, int slave) {
    if (fat_mount_drive(bus, slave) != FS_OK) {
        return "The new AEROS volume couldn't be read back, so the disk didn't take what was written.";
    }
    const char* problem = NULL;
    uint32_t cfg_length = (uint32_t)(_binary_install_grub_cfg_end - _binary_install_grub_cfg_start);
    if (fat_create_dir("/boot/") != FS_OK || fat_create_dir("/boot/grub/") != FS_OK ||
        fat_create_file("/boot/", "main.elf", boot_kernel_file, boot_kernel_file_size) != FS_OK ||
        fat_create_file("/boot/grub/", "grub.cfg", _binary_install_grub_cfg_start, cfg_length) != FS_OK) {
        problem = "Couldn't write /boot/main.elf and /boot/grub/grub.cfg on the new volume.";
    } else {
        prtf("  done\n  checking main.elf on the disk...");
        uint32_t size = 0;
        char* back = fat_read_file("/boot/", "main.elf", &size);
        if (back == NULL || size != boot_kernel_file_size || memcmp(back, boot_kernel_file, size) != 0) {
            problem = "main.elf on the disk isn't the same as the one written: the disk may be failing.";
        }
        free(back);
    }
    fat_unmount();
    return problem;
}

// erase a disk and install AerOS on it (see install.h)
const char* install_to_disk (int number) {
    // the checks, all before anything is written
    if (boot_option != BOOT_OPT_DEMO) {
        return "AerOS can only be installed from demo mode (3 in the boot options), where no disk is in use.";
    }
    // a copy of main.elf from GRUB, which starts like every ELF file: 0x7F, then "ELF"
    const uint8_t* kernel = boot_kernel_file;
    if (kernel == NULL || boot_kernel_file_size < 4 || kernel[0] != 0x7F || kernel[1] != 'E' ||
        kernel[2] != 'L' || kernel[3] != 'F') {
        return "There is no copy of AerOS to install: start AerOS from its CD (main.iso), whose GRUB loads one.";
    }
    install_disk_t disks[INSTALL_DISKS_MAX];
    int count = install_list_disks(disks);
    const install_disk_t* disk = NULL;
    for (int i = 0; i < count; i++) {
        if (disks[i].number == number) {
            disk = &disks[i];
        }
    }
    if (disk == NULL) {
        return "There is no hard disk with that number (disks() lists them).";
    }
    if (disk->megabytes < INSTALL_MIN_MB) {
        return "That disk is too small: AerOS needs one of at least 64 MB.";
    }
    uint32_t core_sectors = (uint32_t)(_binary_obj_install_core_img_end - _binary_obj_install_core_img_start + SECTOR - 1) / SECTOR;
    if (1 + core_sectors > PARTITION_LBA) {
        return "GRUB's core image is too big for the space before the partition (src/Makefile, GRUB_MODULES).";
    }
    uint32_t sectors = disk->megabytes * 2048u;
    if (sectors > LBA28_MAX) {
        sectors = LBA28_MAX; // the rest of a bigger disk can't be reached, so it isn't used
    }
    uint32_t partition_sectors = sectors - PARTITION_LBA;

    prtf("Installing AerOS on disk %d (%s, %u MB):\n", disk->number, disk->model, (unsigned int)disk->megabytes);
    prtf("  the boot sector and GRUB...");
    if (!write_grub(disk->bus, disk->slave, partition_sectors)) {
        return "The disk refused the write: nothing more was done.";
    }
    prtf("  done\n  a FAT32 volume labelled AEROS...");
    if (!format_fat32(disk->bus, disk->slave, PARTITION_LBA, partition_sectors)) {
        return "The disk refused a write while making the volume.";
    }
    prtf("  done\n  /boot/main.elf (%u KB) and /boot/grub/grub.cfg...", (unsigned int)(boot_kernel_file_size / 1024));
    const char* problem = write_files(disk->bus, disk->slave);
    if (problem != NULL) {
        return problem;
    }
    if (!drive_flush(disk->bus, disk->slave)) {
        return "The disk didn't confirm everything is written.";
    }
    prtf("  done\n");
    return NULL;
}
