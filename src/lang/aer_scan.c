/*
    aer_scan.c - what an AerLang program can do, found by reading it before it runs (SCAN)

        scan game

        game.aer can:
          read files (rdf, la)
              asks before reading your files
          delete files (delf)
              asks you first
        No program can run terminal commands, read the accounts or change AerOS's log.

          ! line 12: it prints something that looks like AerOS's own questions ("[ ? ]")
        1 thing worth a closer look: read it with RD before running it.

    WHY THIS IS EXACT, NOT A GUESS
    An AerLang program can only use a built-in by writing its name: there is no way to build a
    name from text and call it, and names are matched exactly (aer_lexer.c interns them, the
    interpreter compares them). So if "delf" appears nowhere in the file, the program cannot
    delete files. The scan reads the file the way the lexer does, but only as far as it needs:
    comments (# to the end of the line) are skipped, strings ("...", with \" inside) are read as
    text, and every other word is a name. It works on a program with mistakes in it too, as it
    never has to understand the program, only find its words.

    WHAT IT CAN'T SEE
    A program that writes another program (wrf) and queues it (queue) can make that one do
    anything. The scan says so, and the other program can be scanned too. And whatever the scan
    says, a program still can't do anything the permission system (sys/perm.h) doesn't allow.

    "Worth a closer look" is different: a few patterns in a program's text that suggest it is
    trying to fool you, like imitating AerOS's own questions or login screen. They are hints,
    not proof: read the program (RD) and decide.

    ADDING A BUILT-IN
    If it lets a program do something worth knowing about (touch files, the network, the screen,
    the log...), add its name to a row of abilities[] below, or add a row.
*/

#include "aerlang.h"
#include <stdint.h>
#include "../lib/aer_string.h"
#include "../lib/aer_ctype.h"
#include "../lib/aer_stdio.h"
#include "../lib/primary_definitions.h"

// ---- what each built-in lets a program do -------------------------------------------------------

#define ABILITY_NAMES_MAX 8

typedef struct {
    const char* can;                          // what it lets the program do
    const char* names[ABILITY_NAMES_MAX];     // the built-ins that give it, ending with NULL
    const char* kept_in_check;                // what stops it doing harm, or a warning (or NULL)
} ability_t;

static const ability_t abilities[] = {
    { "read files",                       { "rdf", "rdb", "rdat", "isf", "la", "isenc", "rdenc", NULL },
      "asks before reading your files (AerOS's own it may read)" },
    { "write files",                      { "wrf", "apf", "wrat", "trunc", NULL },
      "asks you first" },
    { "delete files",                     { "delf", NULL },
      "asks you first (UNDO brings back files from your home folder)" },
    { "encrypt or decrypt files",         { "enc", "dec", NULL },
      "asks you first" },
    { "use databases",                    { "dbopen", "dbrun", "dbquery", NULL },
      "asks before opening yours" },
    { "send and receive over the network", { "nsend", "nrecv", "httpget", "tcpconnect", "tcprecv", "tcpsend", NULL },
      "only to computers the firewall (FW) allows" },
    { "let other computers connect to it", { "tcplisten", "tcpaccept", NULL },
      "only on a port you allowed with FW SERVE: it is a server" },
    { "ask you to type a secret",         { "inpw", NULL },
      "type your password only into AerOS's own questions, never a program's" },
    { "read and change the clipboard",    { "clip", "setclip", NULL },
      "it can see anything you have copied" },
    { "clear the log or turn logging off", { "logclear", "setlogging", NULL },
      "AerOS asks you itself, and wants your password" },
    { "add to AerOS's log",               { "logadd", NULL },
      "marked as coming from this program" },
    { "queue other programs to run",      { "queue", NULL },
      "scan those too: this scan can't see into them" },
    { "draw on the whole screen",         { "gopen", "video", NULL },
      "it could draw anything, even something that looks like AerOS" },
    { "make sounds",                      { "play", "sload", "splay", "beep", NULL },
      NULL },
    { "read the mouse",                   { "mouse", "mousemove", "mousedown", NULL },
      NULL },
};
#define ABILITY_COUNT ((int)(sizeof(abilities) / sizeof(abilities[0])))
// (aer_scan_result_t has room for AER_SCAN_ABILITIES_MAX of them: this stops the build if not)
_Static_assert(sizeof(abilities) / sizeof(abilities[0]) <= AER_SCAN_ABILITIES_MAX, "aer_scan_result_t needs room for every ability");

// ---- what the scan finds ------------------------------------------------------------------------

typedef struct {
    // for each ability: the first line using it (0: not used), and which of its names were
    // used, as bits (bit n: abilities[a].names[n])
    int ability_line[ABILITY_COUNT];
    uint8_t names_used[ABILITY_COUNT];

    // the first line naming these built-ins (0: none), for the patterns below
    int asks_for_typing;   // input or inpw
    int writes_files;      // wrf or apf
    int queues;            // queue
    int uses_cmd;          // cmd

    // the first line of each pattern in the program's strings (0: none)
    int looks_like_question;   // imitates AerOS's yellow "[ ? ]" questions
    int looks_like_login;      // imitates the login screen
    int mentions_password;     // talks about a password
    int mentions_program;      // names a .aer file
} findings_t;

// does text (length characters) contain pattern, ignoring capitals?
static bool contains (const char* text, size_t length, const char* pattern) {
    size_t pattern_length = slen(pattern);
    for (size_t start = 0; start + pattern_length <= length; start++) {
        size_t i = 0;
        while (i < pattern_length &&
               tolower((unsigned char)text[start + i]) == tolower((unsigned char)pattern[i])) {
            i++;
        }
        if (i == pattern_length) {
            return true;
        }
    }
    return false;
}

// note the first line something was seen on (a later one doesn't replace it)
static void first_line (int* where, int line) {
    if (*where == 0) {
        *where = line;
    }
}

// a name in the program, on `line`: which ability (if any) does it give?
static void found_name (findings_t* f, const char* name, size_t length, int line) {
    char word[32];
    if (length >= sizeof(word)) {
        return; // longer than any built-in's name
    }
    memcpy(word, name, length);
    word[length] = '\0';

    for (int a = 0; a < ABILITY_COUNT; a++) {
        for (int n = 0; abilities[a].names[n] != NULL; n++) {
            if (seq(word, abilities[a].names[n])) {
                first_line(&f->ability_line[a], line);
                f->names_used[a] |= (uint8_t)(1 << n);
            }
        }
    }
    if (seq(word, "input") || seq(word, "inpw")) {
        first_line(&f->asks_for_typing, line);
    }
    if (seq(word, "wrf") || seq(word, "apf")) {
        first_line(&f->writes_files, line);
    }
    if (seq(word, "queue")) {
        first_line(&f->queues, line);
    }
    if (seq(word, "cmd")) {
        first_line(&f->uses_cmd, line);
    }
}

// a string in the program, on `line`: does it match any of the patterns?
static void found_text (findings_t* f, const char* text, size_t length, int line) {
    if (contains(text, length, "[ ? ]") || contains(text, length, "Y = yes")) {
        first_line(&f->looks_like_question, line);
    }
    if (contains(text, length, "Authentication required") || contains(text, length, "Username")) {
        first_line(&f->looks_like_login, line);
    }
    if (contains(text, length, "password")) {
        first_line(&f->mentions_password, line);
    }
    if (contains(text, length, ".aer")) {
        first_line(&f->mentions_program, line);
    }
}

// Read the program's words, the way the lexer does (see the top): skip comments, read strings
// as text, and everything that starts with a letter or _ is a name
static void read_words (findings_t* f, const char* source) {
    int line = 1;
    const char* p = source;
    while (*p != '\0') {
        char c = *p;
        if (c == '\n') {
            line++;
            p++;
        } else if (c == '#') {
            // a comment: to the end of the line
            while (*p != '\0' && *p != '\n') {
                p++;
            }
        } else if (c == '"') {
            // a string: to the closing quote, stepping over \" (strings never span lines)
            const char* start = ++p;
            while (*p != '\0' && *p != '"' && *p != '\n') {
                if (*p == '\\' && p[1] != '\0' && p[1] != '\n') {
                    p++;
                }
                p++;
            }
            found_text(f, start, (size_t)(p - start), line);
            if (*p == '"') {
                p++;
            }
        } else if (isalpha((unsigned char)c) || c == '_') {
            // a name: letters, digits and _
            const char* start = p;
            while (isalnum((unsigned char)*p) || *p == '_') {
                p++;
            }
            found_name(f, start, (size_t)(p - start), line);
        } else if (isdigit((unsigned char)c)) {
            // a number, letters and all (0xFF), so its letters aren't taken for a name
            while (isalnum((unsigned char)*p) || *p == '.') {
                p++;
            }
        } else {
            p++;
        }
    }
}

// ---- the findings, as lines of text -------------------------------------------------------------

// note one thing worth a closer look
static void add_hint (aer_scan_result_t* out, int line, const char* what) {
    if (out->hint_count < AER_SCAN_HINTS_MAX) {
        sfmt(out->hints[out->hint_count], sizeof(out->hints[0]), "line %d: %s", line, what);
        out->hint_count++;
    }
}

// read a program and put what it can do, and what is worth a closer look, into *out (aerlang.h)
void aer_scan_read (const char* source, aer_scan_result_t* out) {
    findings_t f;
    memset(&f, 0, sizeof(f));
    memset(out, 0, sizeof(aer_scan_result_t));
    read_words(&f, source);

    // each ability it has: "delete files (delf)", and what keeps it in check
    for (int a = 0; a < ABILITY_COUNT; a++) {
        if (f.ability_line[a] == 0) {
            continue;
        }
        char* what = out->abilities[out->ability_count];
        size_t size = sizeof(out->abilities[0]);
        scpy(what, abilities[a].can, size);
        scat(what, " (", size);
        bool first = true;
        for (int n = 0; abilities[a].names[n] != NULL; n++) {
            if (f.names_used[a] & (1 << n)) {
                if (!first) {
                    scat(what, ", ", size);
                }
                scat(what, abilities[a].names[n], size);
                first = false;
            }
        }
        scat(what, ")", size);
        scpy(out->kept_in_check[out->ability_count],
             abilities[a].kept_in_check != NULL ? abilities[a].kept_in_check : "", sizeof(out->kept_in_check[0]));
        out->ability_count++;
    }

    // the patterns that suggest it is trying to fool you
    if (f.looks_like_question != 0) {
        add_hint(out, f.looks_like_question, "it prints something that looks like AerOS's own questions");
    }
    if (f.looks_like_login != 0) {
        add_hint(out, f.looks_like_login, "it prints something that looks like the login screen");
    }
    if (f.mentions_password != 0 && f.asks_for_typing != 0) {
        add_hint(out, f.mentions_password, "it asks for a password: only AerOS itself should ask for yours");
    }
    if (f.mentions_program != 0 && f.writes_files != 0 && f.queues != 0) {
        add_hint(out, f.mentions_program, "it writes programs and queues them: those can't be seen here");
    }
    out->cmd_line = f.uses_cmd;
}

// ---- the report SCAN prints ---------------------------------------------------------------------

// scan a program and print what it can do (see aerlang.h)
int aer_scan (const char* source, const char* name) {
    aer_scan_result_t result;
    aer_scan_read(source, &result);

    // what it can do, each with what keeps it in check underneath
    if (result.ability_count == 0) {
        prtf("%s only works with its own values, prints, and reads what you type. It can't\n"
             "touch files, the network, the clipboard, the log or the whole screen.\n", name);
    } else {
        prtf("%s can:\n", name);
        for (int i = 0; i < result.ability_count; i++) {
            prtf("  %s\n", result.abilities[i]);
            if (result.kept_in_check[i][0] != '\0') {
                prtf("      %s\n", result.kept_in_check[i]);
            }
        }
    }
    prtf("No program can run terminal commands, read the accounts or change AerOS's log.\n");
    if (result.cmd_line != 0) {
        prtf("(Line %d uses cmd(), which only works at the prompt: in a program it stops there.)\n",
             result.cmd_line);
    }

    // and what is worth a closer look
    prtf("\n");
    for (int i = 0; i < result.hint_count; i++) {
        prtf("  ! %s\n", result.hints[i]);
    }
    if (result.hint_count == 0) {
        prtf("Nothing looks suspicious.\n");
    } else {
        prtf("%d thing(s) worth a closer look: read it with RD before running it.\n", result.hint_count);
    }
    return result.hint_count;
}
