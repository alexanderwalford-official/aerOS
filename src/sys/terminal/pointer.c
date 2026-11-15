/*
    pointer.c - the mouse pointer at the terminal's prompt

    While the prompt waits for a line, an arrow (or, in VGA text mode, a highlighted cell) shows
    where the mouse is, and follows it. It is only there while the prompt waits:

        cli_init (t_int.c)               the pieces it uses
        ------------------               --------------------------------------------------------
        pointer_begin()                  mouse_start() and mouse_set_area() (drivers/input/mouse.c),
          GetLine(...)                   console_pointer_show() (drivers/video/VGA_linear.c)
            getk() waits for a key  -->  kb_idle_hook = follow_mouse: about 1000 times a second,
                                         move the pointer if the mouse has moved
        pointer_end()                    console_pointer_hide(), mouse_stop()
        HandleInput(the line)            the command or program runs with no pointer and the
                                         mouse off, exactly as before the pointer existed

    Switching the mouse off before each line matters: a program that asks for the mouse
    (AerLang's mouse()) switches it on for itself, with an area the size of its own screen. If
    the terminal left it on, the program would get the terminal's area instead. The pointer's
    position is remembered, so after a command it comes back where it was.

    It only shows the pointer: clicking does nothing yet.
*/

#include "t_int.h"
#include <stdbool.h>
#include "../../drivers/input/IO.h"
#include "../../drivers/input/mouse.h"
#include "../../drivers/video/VGA_linear.h"

static bool showing = false;    // between pointer_begin and pointer_end, with a mouse
static bool remembered = false; // has it been shown before? Then it goes back where it was
static int remembered_x = 0;
static int remembered_y = 0;

// getk() calls this while it waits for a key (kb_idle_hook): put the pointer where the mouse is.
// console_pointer_show does nothing if it hasn't moved, so this is quick
static void follow_mouse (void) {
    int x, y, buttons;
    mouse_position(&x, &y, &buttons);
    console_pointer_show(x, y);
}

// the prompt is about to wait for a line: switch the mouse on and show the pointer
void pointer_begin (void) {
    if (!mouse_start()) {
        return; // no mouse: no pointer
    }
    // the mouse moves over the console's grid, in its own pixels; set_area puts it in the middle
    int width, height;
    console_pointer_area(&width, &height);
    mouse_set_area(width, height);
    if (remembered) {
        mouse_put(remembered_x, remembered_y);
    }
    showing = true;
    follow_mouse();
    kb_idle_hook = follow_mouse;
}

// the line has been typed: hide the pointer and switch the mouse off, remembering where it was
void pointer_end (void) {
    if (!showing) {
        return;
    }
    kb_idle_hook = NULL;
    int buttons;
    mouse_position(&remembered_x, &remembered_y, &buttons);
    remembered = true;
    console_pointer_hide();
    mouse_stop();
    showing = false;
}
