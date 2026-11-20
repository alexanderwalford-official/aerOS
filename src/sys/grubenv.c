/*
    grubenv.c - reading and changing the settings AerOS leaves for GRUB (see grubenv.h)
*/

#include "grubenv.h"
#include "f_sys.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/aer_stdio.h"

// the line GRUB wants first
static const char header[] = "# GRUB Environment Block\n";

// /grubenv as it is now, or NULL if there is no AEROS disk or no /grubenv. free() it
static char* read_block (void) {
    if (fs_mode != FS_MODE_DISK || !fs_file_exists(GRUBENV_FOLDER, GRUBENV_FILE)) {
        return NULL;
    }
    return fs_read_f(GRUBENV_FOLDER, GRUBENV_FILE);
}

// If `line` (up to its '\n' or the end) is "name=...", where its value starts; NULL if not
static const char* value_in (const char* line, const char* name) {
    size_t length = slen(name);
    if (strncmp(line, name, length) == 0 && line[length] == '=') {
        return line + length + 1;
    }
    return NULL;
}

// read a setting (see grubenv.h)
bool grubenv_get (const char* name, char* out, size_t size) {
    out[0] = '\0';
    char* block = read_block();
    if (block == NULL) {
        return false;
    }
    bool found = false;
    // each line in turn: `line` is where it starts, and the next starts after its '\n'
    for (const char* line = block; *line != '\0'; ) {
        const char* end = strchr(line, '\n');
        const char* value = value_in(line, name);
        if (value != NULL && end != NULL) {
            size_t length = (size_t)(end - value);
            if (length >= size) {
                length = size - 1;
            }
            memcpy(out, value, length);
            out[length] = '\0';
            found = true;
            break;
        }
        if (end == NULL) {
            break;
        }
        line = end + 1;
    }
    free(block);
    return found;
}

// change a setting, keeping the others (see grubenv.h)
bool grubenv_set (const char* name, const char* value) {
    if (fs_mode != FS_MODE_DISK) {
        return false; // nowhere to keep it
    }
    // the new block: the header, every other setting as it was, then this one (unless it is
    // being taken out). +1 for the '\0' the text functions add after it
    char block[GRUBENV_SIZE + 1];
    size_t used = (size_t)sfmt(block, sizeof(block), "%s", header);
    char* old = read_block();
    if (old != NULL) {
        for (const char* line = old; *line != '\0'; ) {
            const char* end = strchr(line, '\n');
            if (end == NULL) {
                break; // the padding at the end: no '\n' after it
            }
            size_t length = (size_t)(end - line) + 1;   // with its '\n'
            // a setting (not the header or a comment) that isn't the one being set
            bool keep = line[0] != '#' && strchr(line, '=') != NULL && strchr(line, '=') < end &&
                        value_in(line, name) == NULL;
            if (keep && used + length < GRUBENV_SIZE) {
                memcpy(block + used, line, length);
                used += length;
            }
            line = end + 1;
        }
        free(old);
    }
    if (value[0] != '\0') {
        used += (size_t)sfmt(block + used, sizeof(block) - used, "%s=%s\n", name, value);
        if (used >= GRUBENV_SIZE) {
            return false; // too much for GRUB's 1024 bytes
        }
    }
    // the rest is '#'s, right up to byte 1024
    memset(block + used, '#', GRUBENV_SIZE - used);
    block[GRUBENV_SIZE] = '\0';

    int result;
    if (fs_file_exists(GRUBENV_FOLDER, GRUBENV_FILE)) {
        result = fs_write_bytes(GRUBENV_FOLDER, GRUBENV_FILE, block, GRUBENV_SIZE, false);
    } else {
        result = fs_create_bytes(GRUBENV_FOLDER, GRUBENV_FILE, block, GRUBENV_SIZE);
    }
    return result == FS_OK;
}
