/*
    install.h - putting AerOS on a disk, so a computer starts it without the CD

    Booted from the CD (the ISO), AerOS runs from memory: the CD is only read by GRUB, and the
    files you make go on an AEROS disk if there is one. Installing makes a disk that starts
    AerOS by itself. It is done from demo mode (the boot options, 3), where AerOS doesn't use
    any disk, so the disk being installed to isn't in use: the installer app, /bin/install.aer,
    lists the disks with AerLang's disks() and calls install() for the one you pick.

    WHAT GOES ON THE DISK (everything else on it is erased)

        sector 0             GRUB's boot sector, and a partition table with one partition
        sectors 1 - 2047     GRUB's core image, which reads FAT and starts AerOS (2047 sectors
                             is the gap before the partition: the usual 1 MB)
        sector 2048 on       the partition: a FAT32 volume labelled AEROS, holding
                                 /boot/main.elf          AerOS itself
                                 /boot/grub/grub.cfg     GRUB's menu (src/install/grub.cfg)

    That is all that has to be copied. The first time AerOS starts from the disk it finds an
    AEROS disk with nothing else on it, and makes the rest there as it would on any new disk:
    its folders, the apps in /bin/, the manual (sys/apps.c), and then asks for the first account.

    HOW EACH PART GETS THERE

    - GRUB's boot sector and core image are made when AerOS is built (the Makefile runs
      grub-mkimage) and built into the kernel, so they are written as they are. The core image
      has GRUB's FAT, partition and multiboot modules in it, and looks for its files on "the
      first partition of the disk it started from" ((,msdos1)/boot/grub), so it doesn't matter
      which disk the BIOS thinks it is. The one change made here: the boot sector's check of
      the drive number the BIOS hands it is switched on, as grub-install does for a hard disk.
    - The FAT32 volume is made here (format_fat32), the same way a formatting tool would.
    - main.elf comes from GRUB: grub.cfg on the CD loads a second copy of it as a "module", and
      boot.c keeps where it is (boot_kernel_file). AerOS has no driver for the CD drive, so this
      is how it gets its own file. The two files are then written with the FAT driver (fat.c),
      as files are on an AEROS disk.

    For BIOS PCs (and QEMU). A UEFI PC needs a different partition layout and a GRUB made for
    UEFI, which isn't done yet.
*/
#ifndef INSTALL_H
#define INSTALL_H

#include <stdint.h>
#include <stdbool.h>

#define INSTALL_DISKS_MAX 4    // the four IDE places: primary and secondary, master and slave
#define INSTALL_MIN_MB    64   // the smallest disk installed to (FAT32 needs 65525 clusters)

// one disk that could be installed to
typedef struct {
    int number;           // 1 to 4: primary master, primary slave, secondary master, secondary slave
    int bus;              // 0 primary, 1 secondary
    int slave;            // 0 master, 1 slave
    char model[41];       // what the drive calls itself ("QEMU HARDDISK")
    uint32_t megabytes;   // its size
    const char* holds;    // what is on it now: "AerOS" (an AEROS disk), "files" (anything else),
                          // or "nothing" (its first sector is empty: a new disk)
} install_disk_t;

// The hard disks there are (CD drives aren't listed), in out, and how many. Only reads them
int install_list_disks (install_disk_t out[INSTALL_DISKS_MAX]);

// Erase disk `number` (from install_list_disks) and install AerOS on it (see the top of this
// file), saying how it goes on the screen. NULL if it worked, or a sentence saying why it
// didn't. Only in demo mode, where no disk is in use: anything else is refused before any of
// the disk is touched
const char* install_to_disk (int number);

#endif /* INSTALL_H */
