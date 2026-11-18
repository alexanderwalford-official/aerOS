# Programs ask first

An AerLang program can read AerOS's own files freely, but before it reads your files, or
changes any file at all, it has to ask you:

```
[ ? ] viewer.aer wants to read files in /home/alex/ (cat.bmp)
      Y = yes, this time    A = always    N = no
```

A program that is drawing graphics asks in a box in the middle of the screen instead, and
its picture is left exactly as it was once you answer.

| Answer | Means |
| --- | --- |
| **Y** | yes, until the program ends |
| **A** | yes, from now on: AerOS remembers it |
| **N** (or Esc) | no, until the program ends |

If you say no, the program carries on as if the file wasn't there: `rdf()` gives `nil`,
`wrf()` gives `false`, and so on.

## What needs asking

| The program wants to | Asks? |
| --- | --- |
| read in `/bin/`, `/docs/`, `/src/` or `/sys/` | no, these are AerOS's own files |
| read in its own folder | no |
| read in the folder named after it, next to it (`/apps/viewer/` for `/apps/viewer.aer`) | no, those are its own files: a package's pictures and the like |
| read a file you named when you started it (`read notes.txt`) | no, that was you asking |
| read anything else | yes |
| change or delete any file | yes |
| change files in a folder it has just made (`mkd`) | no, while it runs: nothing of yours is in it yet |
| touch `/acc/`, where the accounts are kept | never allowed, whatever you answer |

A permission covers one folder (not the folders inside it), so a program that reads ten
pictures from the same folder asks once. Being allowed to change files includes reading them.

The terminal's own commands, and code you type at the prompt, never ask: that is you.

A program running in the background (`bg`) can't ask, as you are busy with whatever is in
front: anything that would need asking is refused, and its output says so. Only what you
answered A (always) to counts there, so run it once without `bg` and answer A first.

## The answers you gave

```
perm              what you have allowed always
perm del 2        forget the second one
perm del all      forget them all
```

They are kept in `/acc/perms`, one line each. No program can ever touch `/acc/`, so no
program can give itself permission.

## Before you run a program: SCAN

`scan` reads a program without running it, and says what it can do:

```
scan game

/home/alex/game.aer can:
  read files (rdf, la)
      asks before reading your files (AerOS's own it may read)
  ask you to type a secret (inpw)
      type your password only into AerOS's own questions, never a program's

  ! line 5: it asks for a password: only AerOS itself should ask for yours
1 thing(s) worth a closer look: read it with RD before running it.
```

It is exact, not a guess: a program can only use a built-in function by writing its name, so
if `delf` isn't in the file (outside comments and text), the program can't delete anything.
"Worth a closer look" is different: those are tricks a program might use to fool you, like
printing something that looks like AerOS's own `[ ? ]` questions or the login screen and then
asking for a password. Read such a program with `rd` before you trust it.

A program that writes another program and queues it can make that one do anything: SCAN says
so, and you can scan the other one too.

A program can scan another too, with `scan(text)` in [AerLang](aerlang.md): an installer can show
you what a program could do before it puts it on the disk.

See also: [AerLang](aerlang.md), [Accounts and encryption](security.md).
