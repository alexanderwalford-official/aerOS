/*
    grubenv.h - the settings AerOS leaves for GRUB, in /grubenv on the AEROS disk

    GRUB reads them before AerOS starts (load_env in the disk's grub.cfg, src/install/grub.cfg),
    so they are how AerOS tells GRUB something for the next start:

        aeros_mode=1920x1080x32     the screen size chosen with RES
        aeros_update=new            a new AerOS to try (sys/update.h); GRUB changes it to
                                    "tried" itself (save_env) as it starts it

    The file is a "GRUB environment block", and GRUB is fussy about it: exactly 1024 bytes, a
    fixed first line, then name=value lines, padded out to the end with '#':

        # GRUB Environment Block
        aeros_mode=1920x1080x32
        aeros_update=new
        ##########...

    Setting one setting keeps the others as they are.
*/
#ifndef GRUBENV_H
#define GRUBENV_H

#include <stdbool.h>
#include <stddef.h>

#define GRUBENV_FOLDER "/"
#define GRUBENV_FILE   "grubenv"
#define GRUBENV_SIZE   1024

// Read setting `name` into out ("" if it isn't set). False if there is no AEROS disk or no
// /grubenv, or the setting isn't in it
bool grubenv_get (const char* name, char* out, size_t size);

// Set `name` to `value` ("" takes it out), keeping the others: /grubenv is made if it isn't
// there. False if there is no AEROS disk, or it couldn't be written
bool grubenv_set (const char* name, const char* value);

#endif /* GRUBENV_H */
