# Installing AerOS on a disk

Started from its CD (`main.iso`), AerOS runs from memory, and keeps your files on an AEROS disk
if there is one. Installing it makes a disk that starts AerOS by itself, with no CD.

```
boot from the CD  ->  X on the splash screen, then 3 (demo mode)  ->  INSTALL  ->  restart
```

**Why demo mode.** In demo mode AerOS doesn't use any disk, so the disk being installed to
can't be in use. The two AerLang built-ins underneath only work there:

| Built-in | What it does |
| --- | --- |
| `disks()` | the hard disks there are: `[number, model, megabytes, what is on it]` each, e.g. `[1, "QEMU HARDDISK", 64, "nothing"]`. What is on it is `"nothing"` (a new disk), `"AerOS"` or `"files"`. Works in any mode: it only reads |
| `install(number)` | erases that disk and installs AerOS on it, saying how it goes. `true` if it worked, `false` after saying why not |

`install()` asks you itself before it touches the disk, with AerOS's own yellow question (and
your password, if there are accounts), whatever the program calling it says: no program can
erase a disk on its own. It is refused outside demo mode, and from a program running in the
background. The installer app, `/bin/install.aer`, is the friendly way in: it lists the disks,
lets you pick one, and calls `install()`.

The code is `src/sys/install.c`. Its header, `src/sys/install.h`, has the whole story. Once
installed, AerOS can [update itself](UPDATING.md) from the package server.

## What goes on the disk

Everything that was on the disk is erased.

| Where | What |
| --- | --- |
| sector 0 | GRUB's boot sector, and a partition table with one partition |
| sectors 1 - 2047 | GRUB's core image, which reads FAT and starts AerOS (about 150 KB) |
| sector 2048 on | a FAT32 volume labelled AEROS, holding `/boot/main.elf` (AerOS) and `/boot/grub/grub.cfg` (GRUB's menu, from `src/install/grub.cfg`) |

Only two files are copied. Started from the disk for the first time, AerOS finds an AEROS disk
with nothing else on it, and sets it up as it does any new disk: its folders, the apps in
`/bin/`, the manual, and then it asks for the first account.

## Where each part comes from

- **GRUB's boot sector and core image** are made when AerOS is built: the Makefile runs
  `grub-mkimage` with the modules GRUB needs built in (`GRUB_MODULES`), and both are built
  into the kernel, like the files in `src/disk/`. The core image looks for its menu on the first
  partition of the disk it started from (`(,msdos1)/boot/grub`), so it doesn't matter which disk
  the BIOS thinks it is. `install.c` puts the partition table into the boot sector, and turns on
  its check of the drive number the BIOS hands it, as `grub-install` does for a hard disk.
- **The FAT32 volume** is made by `install.c` itself, the way a formatting tool would: the
  boot sector with its BIOS parameter block, FSInfo, two FATs and an empty root folder holding
  the label. Cluster sizes follow Microsoft's table, and the disk must be at least 64 MB.
- **`main.elf`** comes from GRUB on the CD: its `grub.cfg` loads a second copy of the kernel as
  a *module* (`module /boot/main.elf`), and `boot.c` keeps where it is (`boot_kernel_file`).
  AerOS has no driver for CD drives, so this is how it gets its own file. The heap is kept out
  of that memory. The two files are then written with the FAT driver, and `main.elf` is read
  back and compared before the installer says it is done.

## Limits

- **BIOS PCs only** (and QEMU). A UEFI PC needs a different partition layout (GPT, with an EFI
  system partition) and a GRUB made for UEFI, which isn't done yet.
- **IDE disks only**, as everywhere in AerOS, and up to 128 GB is used: `ata.c` numbers sectors
  with 28 bits.
- GRUB on the CD loads the copy of `main.elf` on every boot, as it can't know yet whether demo
  mode will be picked, which takes it a moment. Outside demo mode nothing can use the copy, so
  once the boot options are chosen its memory (about 2.6 MB) joins the heap
  (`give_back_kernel_file` in `boot.c`). The computer, or QEMU, still had to find room for it
  while GRUB loaded it.

## Testing it

`tools/test/tests/test_install.py`:
- installs onto a blank 64 MB disk, with the kernel handed over by QEMU's `-initrd` (as GRUB
  would) and `demo` on its command line;
- checks the two files on the disk byte for byte;
- starts QEMU from that disk alone, and checks AerOS comes up in DISK mode and sets the disk up.

Another test does the same from the real CD, picking demo mode in the boot options, and one
checks `install()` refuses, and writes nothing, outside demo mode.
