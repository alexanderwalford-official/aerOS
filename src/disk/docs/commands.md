# Terminal commands

Commands can be typed in upper or lower case. Words in `<angle brackets>` are what you
fill in, and `[square brackets]` mean it can be left out. `HLP` shows this list too.

Press **Tab** to finish a word: the first word becomes a command or app, any later word a
file or folder (after `sd`, only folders). When several things fit, Tab lists them.

Press **Page Up** and **Page Down** to scroll back through what a command printed. The last
400 lines are kept, a tag in the top right says how far back you are, and any other key
takes you back to the prompt. Logging out forgets them.

Anything that isn't a command or an app runs as AerLang, so `2 + 2` or `let x = 5` work
right at the prompt: see [The prompt speaks AerLang](shell.md). A mistyped command gets a
suggestion (`Did you mean DEL?`), and `why` explains any problem.

## Files and folders

| Command | Does |
| --- | --- |
| `la` | list the files and folders here |
| `sd <dir>` | go to a folder: `sd /bin`, `sd ..`, `sd /` |
| `rd <file>` | show a file (asks for the password if it is encrypted) |
| `sql <file> [sql]` | run SQL on a database, or open a `sql>` prompt ([more](database.md)) |
| `mkf <file> [text]` | make a file, with some text in it if you like |
| `wr <file> <text>` | add a line to the end of a file |
| `mkd <dir>` | make a folder |
| `del <name>` | delete a file, or a folder and everything in it (asks first) |
| `vers [file]` | a file's [old versions](versions.md); on its own, every file that has some |
| `undo <file> [n]` | bring a file back as it was (or back from being deleted) |
| `edt <file>` | open a file in the [editor](editor.md) |
| `read [file]` | open a file in the [reader](reader.md) |
| `doc [topic]` | this manual, e.g. `doc sound` |

## Asking AerOS

| Command | Does |
| --- | --- |
| `why` | explain the last thing that went wrong ([more](why.md)) |
| `why <name>` | explain a command, an AerLang function or a C function: `why del` |
| `src <name>` | show the code behind one, with line numbers: `src del`, `src fat.c` |
| `show <command>` | run a command, then see step by step what AerOS did inside: `show del notes.txt` ([more](why.md)) |
| `perm [del <n>]` | what [programs may always do](perms.md) with your files, or forget it |

## Encrypted files

| Command | Does |
| --- | --- |
| `enc <file>` | lock a file with a password |
| `dec <file>` | unlock it again |

See [Accounts and encryption](security.md).

## Accounts

| Command | Does |
| --- | --- |
| `me` | who is logged in |
| `usrs` | list the accounts |
| `mku <name>` | make a new account and its home folder |
| `delu <name>` | delete an account (asks for your password) |
| `pw` | change your password |
| `out` | log out, back to the login screen |

### Your startup commands

`startup.txt` in your home folder runs each time you log in, a line at a time, as if you had
typed each line at the prompt: commands, apps and AerLang all work. `edt startup.txt` changes it.
A new account starts with an example in it, all of it `#` comments, so nothing runs until you
take a `#` off:

```
# startup.txt
fw web packages.intercon.network 80
update check
bg webserve 8080 /docs/
desktop
```

Ctrl+C stops it if something goes wrong. It can't restart, shut down or log out (`rbt`, `off`
and `out` lines are skipped: they would happen again at every login), and no program can
change it, however you have [answered](perms.md): only you, at the prompt.

## Programs

| Command | Does |
| --- | --- |
| `<app> [args]` | run `/bin/<app>.aer` (or an installed package, `/apps/<app>.aer`), e.g. `hello` |
| `aer` | the AerLang prompt |
| `sysinfo` | the system information screen: processor, memory, disks, devices |
| `run <code>` | run code as a process: `run s:hi` |
| `que <app> [args]` | line a program up to run later (`que` lists them, `que clear` empties it) |
| `scan <app>` | what a program can do, read before it runs, and any tricks in it (see [Programs ask first](perms.md)) |
| `go` | run everything queued, in order (Ctrl+C stops the rest) |

AerOS runs one program at a time, so instead of running several together you can queue them
and let `go` run them one after another:

```
que hello
que s:--- now the game ---
que guess
go
```

`que s:<text>` queues a line of text to print, handy between programs.

## Packages

More apps come from the package server, when you ask for them: `pkg` fetches one, checks it
was signed by the packages' maker, shows you what it could do, and asks before installing it.

| Command | Does |
| --- | --- |
| `pkg list` | what there is |
| `pkg install <name>` | check one, show what it could do, and install it |
| `pkg update [name]` | get newer versions of what you have (or of one) |
| `pkg remove <name>` | take one off again, with its folder |
| `pkg installed` | what you have |

The first time, let AerOS reach the server: `fw web packages.intercon.network 80`. There is:

| Package | What it is |
| --- | --- |
| `snake` | Snake, with high scores |
| `maze3d` | a maze in 3D, seen from inside: a pitch black stone dungeon, seen by flashlight (or lantern light, or plain colours for a slow computer); choose its size, find the crystals, dodge the ghosts, with music and sound |
| `move` | move a square with the arrow keys: a small example of a game |
| `guess` | guess the number |
| `viewer` | a picture viewer, with two pictures to try it on |
| `clip` | a 6 second [video](video.md) |
| `webserve` | a web server: show a folder to web browsers ([network](network.md)) |
| `browser` | a web browser for http:// pages: `browser frogfind.com` ([network](network.md)) |
| `bench` | how fast the disk is |
| `anim` | the animations AerLang can draw |
| `desktop` | a desktop with windows: a clock, files, notes, a calculator, your installed apps, Sleep / Restart / Shut down, and Settings for its colours, background picture and taskbar (from the CD, for its graphics screen) |

A package is its program, `/apps/<name>.aer`, and a folder of its own, `/apps/<name>/`, for the
files it comes with (the viewer's pictures) and what it keeps there (Snake's high scores). It
may read its own folder without asking.

The desktop shows every app in `/bin/` and `/apps/` as an icon and in its start menu. Opening
one closes the desktop until the app ends, then it comes back with the same windows. Its
settings are kept in `desktop.cfg` in your home folder.
| `pm` | show the process stack |

## Updating aerOS

On a disk aerOS was installed on (`install`), `update` gets a newer aerOS from the same server
as the packages. It shows the one you have and the newer one, asks, downloads it, and asks
once more, with your password, before it changes anything. `rbt` then starts the new one, and
if it doesn't start, the next restart goes back to the one before by itself.

| Command | Does |
| --- | --- |
| `update` | check, show, ask and update |
| `update check` | say in one line if there is a newer aerOS, and nothing if there isn't |

`update check` stays quiet when it can't tell (no network, or not installed), so it is made
for your startup commands (above): put it in `startup.txt` to hear about
updates each time you log in.

## Sound and screen

| Command | Does |
| --- | --- |
| `ply <file>` | play a WAV or MP3 file, or an .mpg [video](video.md); Esc stops it |
| `bp [hz]` | beep the PC speaker |
| `res [WxH]` | show or change the resolution: `res 1280x720` |
| `cls` | clear the screen |
| `ech <text>` | print some text |

## The network

| Command | Does |
| --- | --- |
| `net` | the card, the address, and what was sent and kept out |
| `net ip <address> <gateway> <dns>` | set AerOS's address: `net ip 10.0.2.15/24 10.0.2.2 10.0.2.3` |
| `fw` | what the firewall allows (nothing, to start with) |
| `fw allow <address or name> <port>` | allow one computer on one port, with a shared password |
| `fw web <address or name> [port]` | allow fetching web pages from one computer (port 80 unless given) |
| `fw serve <port> [address]` | allow other computers (anyone, or one) to connect to AerOS's own port, for a [web server](network.md) |
| `fw del <n>` | block it again |
| `msg <address> <port> <text>` | send a sealed message |
| `lsn <port>` | show the messages that arrive on a port, until Esc |
| `get <url> [file]` | fetch a web page: show it, or save it in a file (`get http://example.com/`) |

See [The network](network.md).

## The computer

| Command | Does |
| --- | --- |
| `clk` | the date and time |
| `up` | how long since it started |
| `mem` | memory use |
| `thr` | the [threads](inside.md) taking turns on the processor: what each is doing, how long it has run, how much of its stack it has used |
| `bg <program> [args]` | run a program in the background while you carry on at the prompt. What it prints shows up at the prompt, marked with its name. `bg` on its own lists them. It can't use the keyboard, the screen or sound, or ask before touching your files (only "always" answers count) |
| `stop <id>` | stop a program running in the background (`bg` shows the number) |
| `rt [stress]` | [RT mode](rt.md)'s real-time tasks (experimental, picked in the boot options) |
| `drv` | the disks AerOS can see |
| `sync` | put every change still held in memory onto the disk now, and show the [disk cache](inside.md) at work (AerOS does this by itself before every prompt, so it is never needed) |
| `rbt` | restart |
| `off` | turn off |

Press **Ctrl+C** to stop a program that has got stuck.
