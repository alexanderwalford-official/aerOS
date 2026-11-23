#!/usr/bin/env python3
"""
make_desktop_browser.py - copy the web browser's engine from browsgui.aer into desktop.aer

    python3 tools/make_desktop_browser.py
    python3 tools/make_desktop_browser.py --check    (only says whether the copy is up to date)

The desktop has a Browser window. It is the same browser as the browsgui package (packages/
browsgui.aer): the same reading of a page's HTML, laying it out in lines, fetching pictures, and
offering to allow blocked sites. But AerLang has no way to share code between two programs, and
the desktop is one program, so the browser's engine is copied into packages/desktop.aer, between
two marker lines, by this script. Run it again after changing the engine in browsgui.aer.

What is copied is everything from the tables at the top of browsgui.aer to its "drawing" section:
reading the page, laying it out, fetching it. Each name in it is given a prefix, so it can't
clash with the desktop's own (both have a layout(), a click(), a site...): functions and
variables get wb_ in front, and the constants WB_ ("Layout" becomes wb_layout, MARGIN WB_MARGIN).
What is drawn, and the mouse and keys, are not copied: in a window they work differently, so
those are written by hand in desktop.aer ("The Browser window"), and use the names this makes.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSER = os.path.join(ROOT, 'packages', 'browsgui.aer')
DESKTOP = os.path.join(ROOT, 'packages', 'desktop.aer')

BEGIN = '# ==== BEGIN: copied from browsgui.aer by tools/make_desktop_browser.py, do not edit here ===='
END = '# ==== END: copied from browsgui.aer ===='

# variables of browsgui.aer that are the whole screen's, or its mouse's, which the desktop has of
# its own (the window's inside is what the browser knows as its screen: WB_W)
DROPPED_LETS = {'SW', 'SH', 'TOOLBAR', 'STATUS_H', 'SUGGEST_H', 'pointer_x', 'pointer_y',
                'buttons_before', 'dragging', 'drag_offset', 'running'}
# names in the copied part that belong to the desktop, not the browser
KEPT = {'draw_all'}
# what the browser calls its screen's width, in a window
RENAMED = {'SW': 'WB_W'}


def split_code(line):
    """A line as (code, rest): rest is the comment, if any. Quoted text is never taken for one."""
    in_text = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_text:
            if c == '\\':
                i += 1
            elif c == '"':
                in_text = False
        elif c == '"':
            in_text = True
        elif c == '#':
            return line[:i], line[i:]
        i += 1
    return line, ''


def rename_code(code, names):
    """Rename names in code, leaving what is inside quotes alone."""
    out = []
    in_text = False
    word = ''
    i = 0

    def flush():
        nonlocal word
        if word:
            out.append(names.get(word, word))
            word = ''
    while i < len(code):
        c = code[i]
        if in_text:
            out.append(c)
            if c == '\\' and i + 1 < len(code):
                out.append(code[i + 1])
                i += 1
            elif c == '"':
                in_text = False
        elif c == '"':
            flush()
            out.append(c)
            in_text = True
        elif c.isalnum() or c == '_':
            word += c
        else:
            flush()
            out.append(c)
        i += 1
    flush()
    return ''.join(out)


def make():
    """The text desktop.aer should have: what it has now, with the copied part made again."""
    lines = open(BROWSER, encoding='utf-8').read().split('\n')

    # the part that is copied: from the table of entities to the drawing section
    start = next(i for i, line in enumerate(lines) if line.startswith('let ENTITIES'))
    while lines[start - 1].startswith('#'):
        start -= 1
    stop = next(i for i, line in enumerate(lines) if line.startswith('# ---- drawing'))
    part = lines[start:stop]

    # every name the browser defines at the top level of its file (all of them, not only in this
    # part, so a name used by the hand written part is also made)
    names = dict(RENAMED)
    for line in lines:
        match = re.match(r'(?:func|let)\s+([A-Za-z_]\w*)', line)
        if match:
            name = match.group(1)
            if name in KEPT or name in DROPPED_LETS:
                continue
            names[name] = ('WB_' if name.isupper() else 'wb_') + name
    # constants are upper case with digits or underscores too (C_PAGE): the prefix is the same
    for name in list(names):
        if name not in RENAMED and re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
            names[name] = 'WB_' + name

    out = []
    skipping = False
    for line in part:
        code, comment = split_code(line)
        # leave out the lets of what the desktop has of its own
        match = re.match(r'let\s+([A-Za-z_]\w*)', code)
        if match and match.group(1) in DROPPED_LETS:
            continue
        out.append(rename_code(code, names) + comment)
    block = [BEGIN, '# (see "The Browser window" for what is written by hand: drawing it, the mouse and the keys)', ''] + out
    while block[-1] == '':
        block.pop()
    block += ['', END]

    desktop = open(DESKTOP, encoding='utf-8').read().split('\n')
    if BEGIN in desktop:
        a = desktop.index(BEGIN)
        b = desktop.index(END)
        desktop[a:b + 1] = block
    else:
        sys.exit('desktop.aer has no "%s" marker line yet: put one where the engine should go' % BEGIN)
    return '\n'.join(desktop), len(out)


def main():
    text, count = make()
    if '--check' in sys.argv:
        # for the tests: is the copy in desktop.aer the one browsgui.aer makes now?
        if text != open(DESKTOP, encoding='utf-8').read():
            sys.exit('the browser in desktop.aer is out of step with browsgui.aer: '
                     'run tools/make_desktop_browser.py')
        print('desktop.aer has the browser as browsgui.aer is now')
        return
    open(DESKTOP, 'w', encoding='utf-8', newline='\n').write(text)
    print('copied %d lines of the browser into %s' % (count, DESKTOP))


if __name__ == '__main__':
    main()
