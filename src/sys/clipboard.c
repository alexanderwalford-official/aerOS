/*
    clipboard.c - the clipboard (see clipboard.h for what uses it)

    It is one fixed buffer of text. Copying replaces it; pasting types it into the line being
    edited, a character at a time, through kb_type in drivers/input/IO.c. Pasting that way means
    the line editor does all the work it already does for typing: inserting in the middle, the
    line's length limit, redrawing.
*/

#include "clipboard.h"
#include "../lib/aer_string.h"
#include "../lib/crypto.h"
#include "../drivers/input/IO.h"
#include "trace.h"

static char clipboard[CLIPBOARD_MAX];

// put text on the clipboard (see clipboard.h)
void clipboard_set (const char* text) {
    scpy(clipboard, text, sizeof(clipboard));
    TRACE("copied %u character(s) to the clipboard", (unsigned int)slen(clipboard));
}

// what is on the clipboard (see clipboard.h)
const char* clipboard_get (void) {
    return clipboard;
}

// paste it into the line being typed (see clipboard.h)
bool clipboard_paste (void) {
    if (clipboard[0] == '\0') {
        return false;
    }
    for (const char* c = clipboard; *c != '\0'; c++) {
        // A line break would finish the line and run it, and a tab would ask for Tab completion:
        // pasting should only ever put text in, so both become spaces
        char key = *c;
        if (key == '\n' || key == '\r' || key == '\t') {
            key = ' ';
        }
        if (!kb_type((uint8_t)key)) {
            break; // too much at once: the rest is left out
        }
    }
    return true;
}

// empty it, wiping the old text (see clipboard.h)
void clipboard_clear (void) {
    wipe(clipboard, sizeof(clipboard));
}
