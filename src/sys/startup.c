/*
    startup.c - running each user's startup.txt after they log in (see startup.h for what it is,
    and what it can't do)

    startup_run reads the whole file first, then hands its lines to the terminal one at a time
    (HandleInput, the same as a line typed at the prompt), showing each before it runs:

        [ * ] Running startup.txt (Ctrl+C stops it):
        > bg webserve 8080 /docs/
        Started webserve in the background, as thread 3 ...
        > desktop
*/

#include "startup.h"
#include "f_sys.h"
#include "syslog.h"
#include "accounts/auth.h"
#include "terminal/commands.h"
#include "terminal/t_int.h"
#include "../drivers/video/VGA_linear.h"
#include "../lang/aerlang.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_stdio.h"
#include "../lib/aer_ctype.h"

// what a new account's startup.txt says: nothing runs until a # is taken off
static const char example[] =
    "# startup.txt: commands that run each time you log in, one a line, as if you\n"
    "# typed them at the prompt. Lines starting with # are skipped, so take the #\n"
    "# off a line to use it. Ctrl+C stops it, if something goes wrong.\n"
    "#\n"
    "# print(\"Welcome back!\")\n"
    "# fw web packages.intercon.network 80\n"
    "# bg webserve 8080 /docs/\n"
    "# desktop\n";

// write the example in a new home folder (see startup.h)
void startup_make_example (const char* home) {
    if (!fs_file_exists(home, STARTUP_FILE)) {
        fs_create_f(home, STARTUP_FILE, example);
    }
}

// is this someone's startup.txt: STARTUP_FILE, in a folder straight inside /home/? (see startup.h)
bool startup_is_the_file (const char* folder, const char* name) {
    const char* homes = "/home/";
    if (strcasecmp(name, STARTUP_FILE) != 0 || strncasecmp(folder, homes, slen(homes)) != 0) {
        return false;
    }
    // what comes after "/home/" must be one folder's name and its "/": "alex/", not "alex/pics/"
    const char* rest = folder + slen(homes);
    const char* slash = strchr(rest, '/');
    return slash != NULL && slash != rest && slash[1] == '\0';
}

// may this line run from startup.txt? Not one that restarts, shuts down or logs out (startup.h)
static bool allowed (const char* line) {
    // the first word, which names the command (if the line is one)
    char word[16];
    size_t length = 0;
    while (line[length] != '\0' && !isspace((unsigned char)line[length]) && length < sizeof(word) - 1) {
        word[length] = line[length];
        length++;
    }
    word[length] = '\0';
    const command_t* command = find_command(word);
    return command == NULL || (command->run != cli_reboot && command->run != cli_shutdown &&
                               command->run != cli_logout);
}

// run the logged in user's startup.txt (see startup.h)
void startup_run (void) {
    char home[FS_PATH_MAX];
    if (fs_mode != FS_MODE_DISK || !auth_home(auth_user, home, sizeof(home)) ||
        !fs_file_exists(home, STARTUP_FILE)) {
        return;
    }
    char* contents = fs_read_f(home, STARTUP_FILE);
    if (contents == NULL) {
        return;
    }

    aer_take_stopped();        // forget a Ctrl+C from before
    bool announced = false;
    int ran = 0;
    // each line in turn: strtok_r cuts the text up at the line breaks
    char* save = NULL;
    for (char* line = strtok_r(contents, "\r\n", &save); line != NULL; line = strtok_r(NULL, "\r\n", &save)) {
        // the line without the spaces around it; blank lines and # comments are skipped
        while (isspace((unsigned char)*line)) {
            line++;
        }
        if (line[0] == '\0' || line[0] == '#') {
            continue;
        }
        if (!announced) {
            colour(LIGHT_GREEN, BLACK);
            prtf("[ * ] Running %s (Ctrl+C stops it):\n", STARTUP_FILE);
            colour(BRIGHT, BLACK);
            announced = true;
        }
        if (ran == STARTUP_LINES_MAX) {
            prtf("[ ! ] %s has more than %d lines: the rest are left.\n", STARTUP_FILE, STARTUP_LINES_MAX);
            break;
        }
        // show it as it would look typed at the prompt, then run it the same way
        colour(DARK_GRAY, BLACK);
        prtf("> %s\n", line);
        colour(BRIGHT, BLACK);
        if (!allowed(line)) {
            prtf("[ ! ] Skipped: %s can't restart, shut down or log out (it would run again, for ever).\n",
                 STARTUP_FILE);
            continue;
        }
        // HandleInput cuts the line up as it reads it, so it gets a copy
        char copy[CLI_INPUT_MAX];
        scpy(copy, line, sizeof(copy));
        HandleInput(copy);
        ran++;
        // Ctrl+C while that ran: the rest of the file isn't run either
        if (aer_take_stopped()) {
            prtf("[ ! ] Stopped %s (Ctrl+C): the rest of it wasn't run.\n", STARTUP_FILE);
            break;
        }
    }
    free(contents);
    if (ran > 0) {
        syslog_add("boot", "startup", "ran %s's %s (%d line(s))", auth_user, STARTUP_FILE, ran);
    }
}
