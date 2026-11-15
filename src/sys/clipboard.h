/*
    clipboard.h - the clipboard: one piece of text, kept to be pasted somewhere else

    What puts text on it, and what takes it off:

        selecting text with the mouse at the prompt     -> clipboard_set   (sys/terminal/pointer.c)
        right-click, or Ctrl+V while typing a line      -> clipboard_paste (drivers/input/IO.c)
        setclip(text) and clip() in AerLang             -> clipboard_set / clipboard_get
                                                           (lang/bi_text.c)

    It holds plain text only, up to CLIPBOARD_MAX - 1 characters (a whole screen is 80 x 25 plus
    a line break at the end of each row, so it always fits). It is emptied when someone logs out,
    like the history of typed lines, so the next person can't paste what you copied.
*/

#ifndef CLIPBOARD_H
#define CLIPBOARD_H

#include <stdbool.h>

#define CLIPBOARD_MAX 2048

// put text on the clipboard, replacing what was there (cut short if it is too long)
void clipboard_set (const char* text);

// what is on the clipboard: "" when nothing is. Stays good until the next clipboard_set
const char* clipboard_get (void);

// Paste the clipboard into the line being typed, as if it were typed in: each character goes
// through the line editor (drivers/input/IO.c's kb_type). A line break or tab becomes a space,
// so pasting can't run a command by itself. False if there is nothing to paste
bool clipboard_paste (void);

// empty it (logging out), wiping the old text from memory
void clipboard_clear (void);

#endif /* CLIPBOARD_H */
