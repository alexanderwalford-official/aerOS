/*
    bi_system.c
    AerLang's built-in functions for the system: waiting (sleep), the time, random numbers,
    what AerOS knows about the computer (sysinfo), queueing programs for GO (queue), running a
    terminal command from the prompt (cmd), what app mode did (appmode), adding to AerOS's log
    (logadd), clearing it or turning it off after asking (logclear, setlogging, islogging),
    the disks there are and installing AerOS on one (disks, install: see sys/install.h),
    starting an installed app in the program's place (launch), turning the computer off or
    restarting it after asking (power),
    updating AerOS on an installed disk (osbuild, osversion, osupdatable, osupdate: see
    sys/update.h) and ending the program (exit).

    Which built-ins need which part of AerOS, for app mode, is listed next to aer_profile in
    aer_builtins.c.

    How a built-in works, and how to add one, is at the top of builtins.h.
*/

#include "builtins.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_stdio.h"
#include "../lib/aer_ctype.h"
#include "../drivers/video/VGA_linear.h"
#include "../drivers/input/IO.h"
#include "../sys/sys_clck.h"
#include "../sys/sysinfo.h"
#include "../sys/p_man.h"
#include "../sys/terminal/t_int.h"
#include "../sys/appmode.h"
#include "../sys/syslog.h"
#include "../sys/thread.h"
#include "../sys/f_sys.h"
#include "../sys/accounts/auth.h"
#include "../sys/install.h"
#include "../sys/update.h"
#include "../sys/boot.h"
#include "../sys/power.h"
#include "../lib/crypto.h"
#include "../drivers/video/gfx.h"

// sleep(ms): wait that many milliseconds (Ctrl+C still stops the program)
value_t bi_sleep (int argc, value_t* argv, int line) {
    (void)argc;
    int32_t ms = int_arg(argv, 0, line, "sleep");
    // sleep in small steps so ctrl + C still gets noticed
    // (20ms at a time, or whatever is left if that is less; ms may end up below 0, which is fine)
    // (other threads, like the disk writer, run meanwhile: sys/thread.h). aer_should_stop is
    // Ctrl+C for the program in front, STOP for one in the background
    while (ms > 0 && !aer_should_stop()) {
        thread_sleep(ms > 20 ? 20 : (uint32_t)ms);
        ms -= 20;
    }
    // time still left means the loop stopped early, so Ctrl+C was pressed
    if (ms > 0) {
        aer_stop(line);
    }
    return aer_nil();
}

// time(): how many milliseconds AerOS has been running, for timing things
value_t bi_time (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    // a whole number only goes up to 2147483647, so after about 24.8 days running this turns negative
    return aer_int((int32_t)clkuptms());
}

// random(n): a random whole number from 0 to n - 1
value_t bi_random (int argc, value_t* argv, int line) {
    (void)argc;
    int32_t range = int_arg(argv, 0, line, "random");
    if (range <= 0) {
        aer_error(line, "random() needs a number above 0");
    }
    // the remainder after dividing by range is always 0 to range - 1. rand() is seeded from
    // the clock in aer_define_constants, so each run gets different numbers
    return aer_int(rand() % range);
}

// add one row of system information to the list as [group, name, value]
// sysinfo_collect calls this once for every row, ctx is the list that sysinfo() made
static void info_row (const char* group, const char* name, const char* value, void* ctx) {
    value_t row = aer_list(3);
    aer_list_push(row.as.obj, aer_string(group, (uint32_t)slen(group)));
    aer_list_push(row.as.obj, aer_string(name, (uint32_t)slen(name)));
    aer_list_push(row.as.obj, aer_string(value, (uint32_t)slen(value)));
    // the push takes over our reference to the row, so there is nothing to release
    aer_list_push((aer_object_t*)ctx, row);
}

// sysinfo(): everything AerOS knows about the computer, as a list of [group, name, value] (see sys/sysinfo.h)
value_t bi_system_info (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    // 64 is a first guess at how many rows there are, the list grows if there are more
    value_t rows = aer_list(64);
    sysinfo_collect(info_row, rows.as.obj);
    return rows;
}

// queue("hello.aer a b"): put a program (and its arguments) at the end of the queue the GO
// command runs, or "s:text" to print a line between programs. Returns how many are now queued,
// or -1 if the process stack is full. The line isn't checked until it runs
value_t bi_queue (int argc, value_t* argv, int line) {
    (void)argc;
    aer_object_t* code = str_arg(argv, 0, line, "queue");
    if (code->length == 0) {
        aer_error(line, "queue() needs a program to queue");
    }
    // p_queue_add copies the line, so the string can go when the program ends
    return aer_int(p_queue_add(code->data.chars));
}

// the most cmd() keeps of what a command prints (the rest is dropped)
#define CMD_OUTPUT_MAX (16 * 1024)

// cmd(line): run a terminal command as if it had been typed at the prompt, and give back what it
// printed as a list of lines, instead of showing it: cmd("la"), len(cmd("mem")).
// Only at the prompt, not in a program: programs have to ask before they touch your files,
// and a terminal command wouldn't. The command can't use the keyboard while it runs, so a
// question it asks gets no answer (DEL's "Delete it? (y/n)" is a no)
value_t bi_cmd (int argc, value_t* argv, int line) {
    (void)argc;
    // aer->shell is only true for the code typed at the terminal's prompt
    if (!aer->shell) {
        aer_error(line, "cmd() only works at the prompt, not in a program");
    }
    // there is only one capture going at a time, so a command that itself ran cmd() would spoil the first
    if (console_capturing()) {
        aer_error(line, "cmd() can't be used inside another cmd()");
    }
    const char* command = str_arg(argv, 0, line, "cmd")->data.chars;
    // run the command with everything it prints going into output instead of onto the screen
    char* output = aer_alloc(CMD_OUTPUT_MAX);
    cli_capture(command, output, CMD_OUTPUT_MAX);

    // one string per line ('\r' and the '\n' left off), with no empty line after the last one
    value_t lines = aer_list(8);
    const char* p = output;
    while (*p != '\0') {
        // strcspn: how many characters there are before the next '\n' (or the end)
        size_t length = strcspn(p, "\n");
        size_t text_length = length > 0 && p[length - 1] == '\r' ? length - 1 : length;
        aer_list_push(lines.as.obj, aer_string(p, (uint32_t)text_length));
        p += length;
        // step over the '\n' itself. Text that ends with one stops the loop here, which is why
        // there is no empty line at the end
        if (*p == '\n') {
            p++;
        }
    }
    aer_free(output);
    return lines;
}

// launch(app, back): when this program ends, run the installed app `app` ("maze3d", or
// "/apps/maze3d.aer": a program in /bin/, /bin/games/ or /apps/, started with nothing after
// its name), and when that ends, start this program again, with `back` (text, "" if left out)
// as its arguments. True if that will happen, false if `app` isn't an app it may start.
// The program ends itself as usual; calling launch() again picks a different app instead.
// AerOS runs one program in front at a time, so this is how the desktop opens apps: see
// "launch()" in aer_interp.c for the rules, and why they keep it from going round the
// permission questions
value_t bi_launch (int argc, value_t* argv, int line) {
    if (aer->shell) {
        aer_error(line, "launch() is for programs: at the prompt, type the app's name to run it");
    }
    const char* app = str_arg(argv, 0, line, "launch")->data.chars;
    const char* back = argc > 1 ? str_arg(argv, 1, line, "launch")->data.chars : "";
    if (slen(back) >= AER_LAUNCH_ARGS_MAX) {
        aer_error(line, "launch()'s second argument is too long (at most %d characters)", AER_LAUNCH_ARGS_MAX - 1);
    }
    return aer_bool(aer_launch_later(app, back) == NULL);
}

// power(what): turn the computer off ("off", what OFF does) or restart it ("restart", what RBT
// does), once AerOS itself has asked the person at the keyboard (perm_confirm: a box over the
// graphics, or a question on the text screen). False if they said no; otherwise it never comes
// back. The disk gets everything first (sys/power.c), but the program must save its own work
// before it asks. There is no password to type, as at the prompt OFF and RBT need none: the
// question is there so the person decides, not the program
value_t bi_power (int argc, value_t* argv, int line) {
    (void)argc;
    const char* what = str_arg(argv, 0, line, "power")->data.chars;
    bool off = strcasecmp(what, "off") == 0;
    if (!off && strcasecmp(what, "restart") != 0) {
        aer_error(line, "power() takes \"off\" or \"restart\", not \"%s\"", what);
    }
    const char* doing = off ? "turn off the computer" : "restart the computer";

    // who is asking: the program's name, or the prompt
    const char* program = perm_program_name();
    char question[FS_NAME_MAX + 48];
    if (program[0] != '\0') {
        sfmt(question, sizeof(question), "%s wants to %s", program, doing);
    } else {
        sfmt(question, sizeof(question), "%c%s?", toupper(doing[0]), doing + 1);   // "Turn off the computer?"
    }
    if (!perm_confirm(question)) {
        return aer_bool(false);
    }
    syslog_add("boot", "power", "%s: %s", program[0] != '\0' ? program : "the prompt", doing);
    // the text screen back, so "It is now safe to turn off your computer" can be seen if the
    // computer doesn't switch itself off
    aer_give_screen_back();
    if (off) {
        sys_shutdown();
    }
    sys_reboot();
}

// appmode(): what app mode did for this program, as [the parts of AerOS it uses, KB of memory
// given back for it, KB free], e.g. [["graphics", "files"], 84, 3900]. It also puts away again
// any part the program doesn't use that has started since. At the prompt, which might use
// anything later, it puts away everything it can (a part starts again by itself when needed)
value_t bi_appmode (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    // at the prompt: start again saying it uses nothing (0), so everything that can be is put away.
    // In a program: keep what it was found to use, and put the rest away again
    if (aer->shell) {
        appmode_begin(0);
    } else {
        appmode_again();
    }
    const appmode_t* mode = appmode_now();

    // the names of the parts in use: every part is one bit (1, 2, 4...), so walk the bits and
    // test each one against the set
    value_t used = aer_list(APP_PARTS);
    for (int bit = 0; bit < APP_PARTS; bit++) {
        uint32_t part = 1u << bit;
        if (mode->uses & part) {
            const char* name = appmode_part_name(part);
            aer_list_push(used.as.obj, aer_string(name, (uint32_t)slen(name)));
        }
    }
    // [used, freed, free], the memory figures turned from bytes into KB
    value_t result = aer_list(3);
    aer_list_push(result.as.obj, used);
    aer_list_push(result.as.obj, aer_int((int32_t)(mode->freed / 1024)));
    aer_list_push(result.as.obj, aer_int((int32_t)(mode->free / 1024)));
    return result;
}

// logadd(message): add an entry to AerOS's log (/sys/logs.db, see sys/syslog.h), of kind "app",
// with the program's name as its source ("snake" for snake.aer). It is written when the
// program ends, so the program itself won't see it with dbquery
value_t bi_logadd (int argc, value_t* argv, int line) {
    (void)argc;
    const char* message = str_arg(argv, 0, line, "logadd")->data.chars;

    // the program's name without ".aer", or "prompt" for code typed at the prompt
    char source[FS_NAME_MAX];
    scpy(source, perm_program_name(), sizeof(source));
    char* dot = strrchr(source, '.');
    if (dot != NULL) {
        *dot = '\0';
    }
    if (source[0] == '\0') {
        scpy(source, "prompt", sizeof(source));
    }
    syslog_add("app", source, "%s", message);
    return aer_nil();
}

// ---- changing AerOS's log: logclear() and setlogging() -------------------------------------------
//
// Programs can't change the log (sys/syslog.h). These two built-ins are the only ways, and AerOS
// asks the person at the keyboard first, not the program: Y or N, then (when there are accounts)
// the logged in user's password. Both ask on the text screen, so not while graphics are on.

// Ask the person at the keyboard whether `what` may happen ("clear AerOS's log"), then for their
// password. `detail` is added to the question only, not the log ("" for none). True only if they
// said Y and typed the right password. `with` is set to what is asking: the program's name, or
// "the prompt". A wrong password is logged
static bool person_agrees (int line, const char* fn, const char* what, const char* detail, char* with, size_t with_size) {
    if (gfx_width() > 0) {
        aer_error(line, "%s() asks for a password on the text screen: call it before gopen() or after gclose()", fn);
    }

    // the question: who wants to do what
    const char* program = perm_program_name();
    char question[FS_NAME_MAX + 96];
    if (program[0] != '\0') {
        scpy(with, program, with_size);
        sfmt(question, sizeof(question), "%s wants to %s%s", program, what, detail);
    } else {
        scpy(with, "the prompt", with_size);
        sfmt(question, sizeof(question), "%c%s%s?", toupper(what[0]), what + 1, detail);   // "Clear AerOS's log ...?"
    }
    if (!perm_confirm(question)) {
        return false;
    }

    // The logged in user's password. With no disk there are no accounts (everyone is "guest"),
    // so there is no password to ask for, and the log is only in memory anyway
    if (auth_required()) {
        char password[64];
        prtf("      Your password (%s): ", auth_user);
        GetSecret(password, sizeof(password));
        bool right = auth_check(auth_user, password);
        // blank it straight away, so the password isn't left in memory for whatever runs next
        wipe(password, sizeof(password));
        if (!right) {
            prtf("      Wrong password: nothing was changed.\n");
            syslog_add("warning", "log", "a wrong password was typed for %s, trying to %s with %s",
                       auth_user, what, with);
            return false;
        }
    }
    return true;
}

// logclear(): clear AerOS's log, after asking (above). Every entry and every earlier boot go
// (syslog_clear in sys/syslog.c), leaving one entry saying who cleared it and with what. Gives
// back true if it was cleared, false if not. The program must have closed the log first:
// clearing it moves its pages, so a copy opened before would read the wrong ones
value_t bi_logclear (int argc, value_t* argv, int line) {
    (void)argc;
    (void)argv;
    if (database_is_open(SYSLOG_FOLDER, SYSLOG_FILE)) {
        aer_error(line, "logclear(): close the log with dbclose() first (and open it again afterwards)");
    }
    char with[FS_NAME_MAX];
    if (!person_agrees(line, "logclear", "clear AerOS's log", " (every entry, and every earlier boot)", with, sizeof(with))) {
        return aer_bool(false);
    }
    return aer_bool(syslog_clear(auth_user, with));
}

// setlogging(on): turn AerOS's logging on (true) or off (false), after asking (above). It stays
// that way after a restart. Gives back true if logging is now the way asked for (also when it
// already was: then nothing is asked), false if not
value_t bi_setlogging (int argc, value_t* argv, int line) {
    (void)argc;
    if (argv[0].type != V_BOOL) {
        aer_error(line, "setlogging() needs true (on) or false (off)");
    }
    bool on = argv[0].as.i != 0;
    if (on == syslog_logging()) {
        return aer_bool(true);
    }
    char with[FS_NAME_MAX];
    const char* what = on ? "turn AerOS's logging back on" : "turn AerOS's logging off";
    if (!person_agrees(line, "setlogging", what, "", with, sizeof(with))) {
        return aer_bool(false);
    }
    return aer_bool(syslog_set_logging(on, auth_user, with));
}

// islogging(): is AerOS's logging on? (true unless setlogging(false) turned it off)
value_t bi_islogging (int argc, value_t* argv, int line) {
    (void)argc;
    (void)argv;
    (void)line;
    return aer_bool(syslog_logging());
}

// disks(): the hard disks there are, to install AerOS on (sys/install.h): a list with one
// [number, model, megabytes, what is on it] for each, e.g. [1, "QEMU HARDDISK", 64, "nothing"].
// What is on it is "nothing" (a new disk), "AerOS" (an AEROS disk) or "files" (anything else).
// It only reads them, so it works in any mode
value_t bi_disks (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    install_disk_t disks[INSTALL_DISKS_MAX];
    int count = install_list_disks(disks);
    value_t list = aer_list((uint32_t)count);
    for (int i = 0; i < count; i++) {
        value_t disk = aer_list(4);
        aer_list_push(disk.as.obj, aer_int(disks[i].number));
        aer_list_push(disk.as.obj, aer_string(disks[i].model, (uint32_t)slen(disks[i].model)));
        aer_list_push(disk.as.obj, aer_int((int32_t)disks[i].megabytes));
        aer_list_push(disk.as.obj, aer_string(disks[i].holds, (uint32_t)slen(disks[i].holds)));
        aer_list_push(list.as.obj, disk);
    }
    return list;
}

// install(number): erase disk `number` (from disks()) and install AerOS on it, saying how it goes.
// Gives back true if it worked; false after saying why not. Only in demo mode, where no disk is
// in use, and only after AerOS itself has asked the person at the keyboard (person_agrees, as
// logclear does): a program can never erase a disk on its own
value_t bi_install (int argc, value_t* argv, int line) {
    (void)argc;
    int32_t number = int_arg(argv, 0, line, "install");
    if (boot_option != BOOT_OPT_DEMO) {
        prtf("[ ! ] AerOS can only be installed from demo mode (3 in the boot options), where no disk is in use.\n");
        return aer_bool(false);
    }
    // the disk, so the question can say which one it is
    install_disk_t disks[INSTALL_DISKS_MAX];
    int count = install_list_disks(disks);
    const install_disk_t* disk = NULL;
    for (int i = 0; i < count; i++) {
        if (disks[i].number == number) {
            disk = &disks[i];
        }
    }
    if (disk == NULL) {
        prtf("[ ! ] There is no hard disk %d (disks() lists them).\n", (int)number);
        return aer_bool(false);
    }
    char detail[96];
    // (short, so with a program's name in front it still fits on one row of the screen)
    sfmt(detail, sizeof(detail), " %d (%s, %u MB)", disk->number, disk->model,
         (unsigned int)disk->megabytes);
    char with[FS_NAME_MAX];
    if (!person_agrees(line, "install", "erase EVERYTHING on disk", detail, with, sizeof(with))) {
        prtf("Nothing was changed.\n");
        return aer_bool(false);
    }
    const char* problem = install_to_disk((int)number);
    if (problem != NULL) {
        prtf("\n[ ! ] %s\n", problem);
        return aer_bool(false);
    }
    syslog_add("boot", "install", "AerOS was installed on disk %d (%s) with %s", disk->number, disk->model, with);
    return aer_bool(true);
}

// ---- updating AerOS: osbuild(), osversion(), osupdatable() and osupdate() (sys/update.h) -----

// osbuild(): this AerOS's build number, the time it was built: "20261204183000" (UTC, year
// first, so a later build is a bigger number, and comparing them as text works too)
value_t bi_osbuild (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    const char* build = update_build();
    return aer_string(build, (uint32_t)slen(build));
}

// osversion(): this AerOS's version, "0.01 ALPHA"
value_t bi_osversion (int argc, value_t* argv, int line) {
    (void)argc; (void)argv; (void)line;
    const char* version = update_version();
    return aer_string(version, (uint32_t)slen(version));
}

// osupdatable(say): can AerOS be updated here? true if so (it was installed on a disk, and started
// from it), or false after saying why not. osupdatable(false) doesn't say why: for checking
// quietly, as "update check" does at each login (from startup.txt)
value_t bi_osupdatable (int argc, value_t* argv, int line) {
    bool say = true;
    if (argc == 1) {
        if (argv[0].type != V_BOOL) {
            aer_error(line, "osupdatable() takes true (say why not, as it does with nothing) or false (don't)");
        }
        say = argv[0].as.i != 0;
    }
    const char* why_not = update_unavailable();
    if (why_not != NULL) {
        if (say) {
            prtf("[ ! ] %s\n", why_not);
        }
        return aer_bool(false);
    }
    return aer_bool(true);
}

// osupdate(kernel, signature): put a new AerOS (a main.elf, as bytes or text, and its signature
// as 128 hex digits) on the disk, to start next time. AerOS checks it first, itself: signed by
// AerOS's makers, newer than the AerOS on the disk, and a kernel GRUB can start (sys/update.h).
// Then it asks the person at the keyboard (person_agrees, as install() does), so a program can
// never update AerOS on its own. Gives back true if it is in place, false after saying why not
value_t bi_osupdate (int argc, value_t* argv, int line) {
    (void)argc;
    value_t kernel = argv[0];
    if (kernel.type != V_STR && kernel.type != V_BYTES) {
        aer_error(line, "osupdate() needs the new AerOS as bytes (httpget gives them), not a %s",
                  aer_type_name(kernel));
    }
    const char* signature = str_arg(argv, 1, line, "osupdate")->data.chars;
    const uint8_t* data = kernel.type == V_STR ? (const uint8_t*)kernel.as.obj->data.chars
                                               : (const uint8_t*)kernel.as.obj->data.bytes;
    uint32_t size = kernel.as.obj->length;

    // everything checked before asking: there is no point saying yes to one that won't be used
    char new_build[UPDATE_BUILD_DIGITS + 1], old_build[UPDATE_BUILD_DIGITS + 1];
    const char* wrong = update_check(data, size, signature, new_build, old_build);
    if (wrong != NULL) {
        prtf("[ ! ] %s\n", wrong);
        return aer_bool(false);
    }
    // (short, so with a program's name in front it still fits on one row of the screen)
    char detail[32];
    sfmt(detail, sizeof(detail), " %s", new_build);
    char with[FS_NAME_MAX];
    if (!person_agrees(line, "osupdate", "update AerOS to build", detail, with, sizeof(with))) {
        prtf("Nothing was changed.\n");
        return aer_bool(false);
    }
    const char* problem = update_install(data, size, signature);
    if (problem != NULL) {
        prtf("[ ! ] %s\n", problem);
        return aer_bool(false);
    }
    syslog_add("boot", "update", "%s updated AerOS to build %s, to start next time", with, new_build);
    return aer_bool(true);
}

// exit(): end the program here, quietly
value_t bi_exit (int argc, value_t* argv, int line) {
    (void)argc; (void)argv;
    // leaving by the error route, but quietly: there is nothing wrong
    (void)line;
    // run_program() in aer_interp.c sees the 2 (an error would be 1) and ends without a message.
    // Like an error, this jumps straight out, so the function never returns a value
    longjmp(aer->error, 2);
}
