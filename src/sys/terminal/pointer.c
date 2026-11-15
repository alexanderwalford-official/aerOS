/*
    pointer.c - the mouse at the terminal's prompt

    While the prompt waits for a line, a pointer (an arrow, or in VGA text mode a highlighted
    cell) follows the mouse, and the mouse does what it does in most terminals:

        left click on the line being typed    puts the text cursor there
        left drag                             selects text anywhere on the screen, and copies it
                                              to the clipboard when the button comes up
        double click                          selects and copies the word under the pointer
        right click                           pastes the clipboard where the text cursor is
        scroll wheel                          looks back through earlier output (like Page Up)

    (Ctrl+V pastes too: that's the line editor, in drivers/input/IO.c.)

    HOW IT FITS TOGETHER

        cli_init (t_int.c)               the pieces it uses
        ------------------               --------------------------------------------------------
        pointer_begin()                  mouse_start() and mouse_set_area() (drivers/input/mouse.c)
          GetLine(...)                   (drivers/input/IO.c)
            getk() waits for a key  -->  kb_idle_hook = follow_mouse, about 1000 times a second:
                                           the pointer   console_pointer_show (VGA_linear.c)
                                           the wheel     mouse_wheel, console_view_scroll
                                           clicks        mouse_next_change, then the functions
                                                         below
        pointer_end()                    console_pointer_hide(), mouse_stop()
        HandleInput(the line)            the command or program runs with no pointer and the
                                         mouse off, exactly as if the pointer didn't exist

    Nothing here edits the line itself. A click on the line types Home and then Right as many
    times as it takes (kb_type), and a paste types the clipboard's characters, so the line
    editor does what it always does with keys. Selecting and copying only read the screen.

    The mouse is switched off before each line runs because a program that asks for the mouse
    (AerLang's mouse()) switches it on for itself, with an area the size of its own screen. If
    the terminal left it on, the program would get the terminal's area instead. Where the
    pointer was is remembered, so after a command it comes back to the same place.
*/

#include "t_int.h"
#include <stdbool.h>
#include <stdint.h>
#include "../../drivers/input/IO.h"
#include "../../drivers/input/mouse.h"
#include "../../drivers/video/VGA_linear.h"
#include "../clipboard.h"
#include "../sys_clck.h"

#define DOUBLE_CLICK_MS 400   // a second click on the same cell within this long is a double click
#define WHEEL_LINES     3     // lines the screen moves for each notch of the scroll wheel

// between pointer_begin and pointer_end, with a mouse
static bool showing = false;

// where the pointer was last time, so it comes back there (remembered once it has been shown)
static bool remembered = false;
static int remembered_x = 0;
static int remembered_y = 0;

// the buttons held, as of the last change read with mouse_next_change
static int buttons_held = 0;

// a left drag: the cell the button went down on (-1: no drag), and whether the pointer has left
// that cell since (only then is it a selection, rather than a click)
static int drag_from = -1;
static bool dragged = false;

// the last left click, to spot a double click: when, and on which cell
static uint32_t last_click_ms = 0;
static int last_click_cell = -1;


// ---- what each mouse action does ---------------------------------------------------------------

// copy whatever is selected to the clipboard
static void copy_selection (void) {
    char text[CLIPBOARD_MAX];
    if (console_selection_text(text, sizeof(text)) > 0) {
        clipboard_set(text);
    }
}

// Select the word under a cell (the letters, digits and symbols around it, up to the spaces on
// either side, on that row), and copy it. Clicking on a space selects nothing
static void select_word (int cell) {
    if (console_char_at(cell) == ' ') {
        return;
    }
    int row_start = cell - cell % COLS;
    int row_end = row_start + COLS - 1;
    int from = cell;
    int to = cell;
    while (from > row_start && console_char_at(from - 1) != ' ') {
        from--;
    }
    while (to < row_end && console_char_at(to + 1) != ' ') {
        to++;
    }
    console_select(from, to);
    copy_selection();
}

// A click (not a drag) on the line being typed: put the text cursor there, by typing Home and
// then Right once for each character along. A click anywhere else does nothing, and so does one
// while Page Up's view of earlier output is showing (those cells aren't the line)
static void place_cursor (int cell) {
    int line_start, line_length;
    if (!getline_line(&line_start, &line_length) || console_viewing_back()) {
        return;
    }
    int offset = cell - line_start;   // which character of the line was clicked
    if (offset < 0 || offset > line_length) {
        return;                       // before the line (the prompt) or past its end
    }
    kb_type(KEY_HOME);
    for (int i = 0; i < offset; i++) {
        kb_type(KEY_RIGHT);
    }
}

// the left button went down on a cell: the start of a click, a drag, or a double click
static void left_pressed (int cell) {
    // a new click drops the old selection
    console_select_none();

    uint32_t now = clkuptms();
    bool double_click = cell == last_click_cell && now - last_click_ms < DOUBLE_CLICK_MS;
    last_click_cell = cell;
    last_click_ms = now;

    if (double_click) {
        select_word(cell);
        drag_from = -1;        // done: letting go of the button does nothing more
        last_click_cell = -1;  // so a third click starts afresh, rather than another double
        return;
    }
    drag_from = cell;
    dragged = false;
}

// the left button came up on a cell: the end of a drag (copy what was selected) or of a click
// (put the cursor there)
static void left_released (int cell) {
    if (drag_from < 0) {
        return; // a double click: already done
    }
    if (dragged) {
        console_select(drag_from, cell);
        copy_selection();
    } else {
        place_cursor(cell);
    }
    drag_from = -1;
}


// ---- following the mouse -----------------------------------------------------------------------

// getk() calls this while it waits for a key (kb_idle_hook). Each time: move the pointer, scroll
// for the wheel, then deal with every press and release since last time, in order. It is called
// about 1000 times a second, so when nothing has happened it does next to nothing
static void follow_mouse (void) {
    // the pointer goes where the mouse is (console_pointer_show does nothing if it hasn't moved)
    int x, y, buttons;
    mouse_position(&x, &y, &buttons);
    console_pointer_show(x, y);

    // the wheel: turned up (away from you, negative) looks back, down comes forward again
    int notches = mouse_wheel();
    if (notches != 0) {
        console_view_scroll(-notches * WHEEL_LINES);
    }

    // each press and release, where the pointer was at the time
    int change_x, change_y, now_held;
    while (mouse_next_change(&change_x, &change_y, &now_held)) {
        int pressed = now_held & ~buttons_held;    // buttons down now that weren't before
        int released = buttons_held & ~now_held;   // and the other way round
        buttons_held = now_held;
        int cell = console_cell_at(change_x, change_y);

        if (pressed & MOUSE_LEFT) {
            left_pressed(cell);
        }
        if (released & MOUSE_LEFT) {
            left_released(cell);
        }
        if (pressed & MOUSE_RIGHT) {
            clipboard_paste();
        }
    }

    // dragging: once the pointer leaves the cell the button went down on, the selection runs
    // from that cell to wherever the pointer is
    if ((buttons_held & MOUSE_LEFT) && drag_from >= 0) {
        int cell = console_cell_at(x, y);
        if (cell != drag_from || dragged) {
            dragged = true;
            console_select(drag_from, cell);
        }
    }
}


// ---- on and off --------------------------------------------------------------------------------

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
    buttons_held = 0;
    drag_from = -1;
    showing = true;
    follow_mouse();
    kb_idle_hook = follow_mouse;
}

// the line has been typed: hide the pointer and the selection, and switch the mouse off,
// remembering where the pointer was
void pointer_end (void) {
    if (!showing) {
        return;
    }
    kb_idle_hook = NULL;
    int buttons;
    mouse_position(&remembered_x, &remembered_y, &buttons);
    remembered = true;
    console_select_none();
    console_pointer_hide();
    mouse_stop();
    showing = false;
}
