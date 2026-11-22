# AerLang

AerLang is the programming language built into AerOS. Programs are text files ending in `.aer`.
The interpreter runs inside the kernel, so a program can use the screen, keyboard and files directly.

## Running programs

| You type | What happens |
| --- | --- |
| `hello` | runs `hello.aer` from the current directory, or from `/bin/` |
| `hello.aer a b` | the same, with `args` set to `["a", "b"]` |
| `run hello.aer` | runs it through the process manager like any other AerLang instruction |
| `let x = 5`, `x * 2` | AerLang right at the terminal's prompt (see "At the prompt" below) |
| `aer` | a prompt where every line is code, even one that looks like a command, `exit` to leave |

Press **Ctrl+C** to stop a program that is stuck in a loop.

### At the prompt

A line typed at the terminal that isn't a command or an app is run as AerLang, in one session
that lasts until you log out, so variables and functions are kept from line to line. The value
of an expression is shown:

```
alex@0//home/alex/: let price = 12
alex@0//home/alex/: price * 3
36
alex@0//home/alex/: func twice(n)
   ... return n * 2
   ... end
alex@0//home/alex/: twice(price)
24
```

A line that opens a block (`if`, `for`, `while` or `func` without its `end`) carries on over
more lines, with `...` as the prompt, until the block is closed (an empty line gives up). The
first word decides what a line is: a command first, then an app, then code. A plain word that
AerLang doesn't know (`frob`, `dle notes.txt`) is taken for a mistyped command and gets a
suggestion, `Did you mean DEL?`. So a variable named like a command (`out`) is shown with
`print(out)`, not by typing its name.

Only at the prompt, `cmd(line)` runs a terminal command and gives back what it printed as a
list of lines, instead of showing it: `len(cmd("la"))`, `cmd("mem")[0]`. The keyboard counts
as unplugged while it runs, so a question the command asks gets no answer (a yes / no question
is a no). Programs can't use `cmd()`: they have to ask before they touch your files, and a
terminal command wouldn't.

### Mistakes

An unknown name gets a suggestion, from the names the program has: `unknown name 'prnt' (did
you mean print?)`. `why` after an error explains it and shows the lines around it, and
`why <function>` (`why gopen`) explains a built-in from the comment above the C code behind
it. `src gopen` shows that code.

AerLang used to be called RenLang, and its programs ended in `.ren`. Those still run if you
type the name with its extension (`old.ren`), and the editor still colours them in.

## Writing programs on AerOS

`edt myapp.aer` opens the editor (the file is made when you first save it). AerLang code is
coloured in, and a new line keeps the indentation of the one above.

| Key | |
| --- | --- |
| Ctrl+S | save |
| Ctrl+R | save and run the program, then press a key to come back |
| Ctrl+Q | quit (press it twice to throw away unsaved changes) |
| arrows, Home, End, Page Up / Down | move around |

Programs can also be written on another computer and copied onto the AerOS disk with
`make disk-put FILE=myapp.aer` (see `src/README.md`). For Visual Studio Code, the
[AerLang extension](https://github.com/alexanderwalford-official/aerlang-vscode) colours AerLang
in too, and suggests keywords, built-ins and their arguments as you type. Its README says how to
install it, and which VS Code and AerOS versions each version of it works with. When a keyword,
built-in or constant is added to AerLang, add it there as well.

The apps in `src/disk/bin/` are built into AerOS and copied to `/bin/` when it starts.

## The language

```
# a comment runs to the end of the line

let name = "AerOS"          # make a variable
name = name + "!"           # change it
print("Hello", name)        # Hello AerOS!

if len(name) > 3 then
    print("long")
elif len(name) == 3 then
    print("three")
else
    print("short")
end

for i = 1 to 10 do          # 1, 2 ... 10 (add "step 2" to count in twos, "step -1" to count down)
    if i % 2 == 0 then continue end
    print(i)
end

for colour in ["red", "green"] do
    print(colour)
end

let n = 0
while n < 3 do
    n = n + 1
end

func area(w, h)
    return w * h
end
print(area(3, 4))           # 12
```

- A statement ends at the end of its line. Short blocks can go on one line: `if x then print(x) end`.
- Variables are made with `let`. Assigning to a name that was never made is an error, which catches typos.
- Variables made with `let` inside a function belong to that function. Everything else is global.
- Functions are made at the top level of a file and can be called before the line that makes them.

### Values

| Type | Examples | Notes |
| --- | --- | --- |
| number | `42`, `-7`, `0xFF` | whole numbers, 32 bit. `/` drops the remainder, `%` is the remainder |
| decimal number | `3.14`, `-0.5`, `2.5e3` | 64 bit floating point (a double), about 15 significant digits |
| string | `"hi\n"` | escapes: `\n` `\t` `\"` `\\` `\0`. `s[0]` is the first character |
| list | `[1, "two", [3]]` | `l[0]`, `l[0] = 5`, `l + [6]` makes a new list |
| bytes | `bytes(100)` | a block of raw bytes (0 to 255), e.g. a file read with `rdb` |
| bool | `true`, `false` | |
| nil | `nil` | "nothing", e.g. what a function returns without `return` |

`nil`, `false` and `0` count as false. `and` / `or` give back one of their values, so
`input("Name? ") or "friend"` works. `+` joins strings, and turns anything else into text when
one side is a string: `"Score: " + 10`.

#### Whole and decimal numbers

A number with a decimal point or an exponent is a decimal (`2.0`, `1e6`); one without is whole.
When both sides of `+ - * / %` are whole, the answer is whole, exactly as it always was:
`7 / 2` is `3`. When either side is a decimal, so is the answer: `7 / 2.0` is `3.5`, and
`10 % 3.5` is `3.0`. Comparisons and `==` work across both (`2 == 2.0` is true), and dividing
by zero is an error either way.

Decimals print with up to six decimal places and at least one (`2.0`, `3.141593`), or with an
exponent when very big or very small (`1.0e+20`, `1.0e-6`). They keep their full precision
inside; only the printing rounds.

Anywhere a built-in function wants a whole number (a screen position, a size, a colour), a
decimal is rounded down, so a game can keep its positions as decimals and still call
`rect(x, y, 10, 10, colour)`. A list index or a `for` loop's numbers can be decimals only if
they are whole (`l[1.0]` is fine, `l[1.5]` is an error).

The decimals are worked out by the processor's x87 FPU, switched on when a program starts
(`lib/fpu.c`); the maths functions are in `lib/aer_math.c`.

### Built-in functions

The short names follow the same pattern as the terminal's commands, so they can be guessed:

| Part | Means | | Part | Means |
| --- | --- | --- | --- | --- |
| `rd` | read | | `f` | file |
| `wr` | write (replace) | | `b` | bytes |
| `ap` | append (add to the end) | | `enc` / `dec` | encrypt / decrypt |
| `del` | delete | | `g` | graphics |
| `is` | is there one? / is it? | | `la` | list all (like the `LA` command) |

So `rdf` reads a file, `apf` appends to one, `isf` asks if a file is there, `rdenc` reads an
encrypted file and `gopen` opens the graphics screen.

**Screen and keyboard**
`print(a, b, ...)` print, separated by spaces, then a new line ·
`write(...)` the same without the new line ·
`input(prompt)` read a line · `inpw(prompt)` the same, showing `*` (for passwords) ·
`clip()` the text on the clipboard, "" if none · `setclip(text)` put text on it (the clipboard
the mouse copies to at the prompt, sys/clipboard.h) ·
`cls()` clear the screen ·
`color(fg, bg)` text colours (`BLACK` ... `WHITE`, and `ORANGE`, the logo orange) ·
`at(x, y)` move the cursor ·
`key()` wait for a key and return its code: a character code, or one of `KEY_UP`, `KEY_DOWN`,
`KEY_LEFT`, `KEY_RIGHT`, `KEY_HOME`, `KEY_END`, `KEY_PGUP`, `KEY_PGDN`, `KEY_DELETE`,
`KEY_ENTER`, `KEY_BACKSPACE`, `KEY_TAB`, `KEY_ESCAPE` ·
`haskey()` true if a key is waiting

**Maths** (angles are in radians: a whole turn is `2 * PI`)
`sqrt(x)` · `sin(angle)` · `cos(angle)` · `atan2(y, x)` the angle to the point (x, y) ·
`floor(x)` rounds down to a whole number · `round(x)` to the nearest · `abs(x)` ·
`float(x)` a number or text as a decimal (`float("2.5")`, nil if it isn't one) ·
`int(x)` a decimal with its decimals cut off (`int(-2.7)` is `-2`), or text as a whole number ·
`PI` (3.14159...)

**Text**
`len(x)` · `str(x)` · `int("42")` (nil if it isn't a number; read as a number in a program is,
so `"0x1F"` is hex and `"08"` is 8) · `chr(65)` · `ord("A")` ·
`sub(s, start, count)` · `find(s, part)` (-1 if missing) · `upper(s)` · `lower(s)` ·
`split(s, sep)` (spaces if no separator) · `join(list, sep)`

**Lists and bytes**
`list(n, fill)` · `push(l, x)` · `pop(l)` · `insert(l, i, x)` · `remove(l, i)` · `bytes(n)`

**Files** (paths can be relative or absolute)
`rdf(path)` (nil if missing) · `rdb(path)` · `wrf(path, text)` (text or bytes, every byte of
it, so a picture fetched with `httpget` can be saved) ·
`apf(path, text)` · `isf(path)` · `delf(path)` ·
`la(dir)` (directories end in `/`) · `cwd()` the current directory, e.g. `"/bin/"` ·
`mkd(path)` make a folder: true if it was made, false if it is there already or can't be (the
program may then change files in it without being asked, until it ends: nothing of yours is in
it yet) · `deld(path)` delete a folder and the files in it (not one with folders inside) ·
`sync()` put every change onto the disk now (AerOS does it within a second anyway, see
[the disk cache](DISK_CACHE.md); for a program that wants to be sure, like a game saving)

**Databases** (AerDB, `src/db/`: tables of rows in one file, and a little SQL)
`dbopen(path)` open a database, making it if need be: a number for it, nil if it can't ·
`dbrun(db, sql, values)` run a statement, giving back how many rows it changed ·
`dbquery(db, sql, values)` run a SELECT: a list of rows, each a list of its values ·
`dbclose(db)` close it. Each `?` in the SQL takes the next of `values`, a list:

```
let db = dbopen("shop.db")
dbrun(db, "CREATE TABLE items (name TEXT, price REAL)")
dbrun(db, "INSERT INTO items VALUES (?, ?)", ["tea", 2.5])
for row in dbquery(db, "SELECT name, price FROM items WHERE price < ?", [3]) do
    print(row[0], row[1])
end
```

The SQL it understands is in the manual inside AerOS (`doc database`); the terminal's `SQL`
command runs it too. How AerDB works inside starts at the top of `src/db/aerdb.h`.

**A piece of a file at a time** (for files changed in place, like a database's pages)
`rdat(path, offset, count)` that many bytes from byte `offset`, as bytes (fewer at the end) ·
`wrat(path, offset, data)` write bytes or text at `offset`, in place, making the file if need
be (past the end makes it longer, and a gap is zeros) · `trunc(path, size)` make it `size`
bytes long. They use `fs_write_at` / `fs_truncate` (`src/sys/f_sys.h`), and keep no old
versions: a database changes pages too often to copy the whole file each time.

`rdf("notes.txt@2")` (and `rdb`, `isf`) reach an old version of a file in a home folder (see
VERS in the manual). A program asks the person at the keyboard before it reads anything
outside `/bin/`, `/docs/`, `/src/`, `/sys/`, its own folder and the files named on its
command line, and before it changes any file at all. If the answer is no, the function fails
as if the file wasn't there (nil or false), and `/acc/` is never allowed. The rules are in
`src/sys/perm.h`.

**At the prompt only**
`cmd(line)` runs a terminal command and gives back what it printed, as a list of lines

**App mode** (src/sys/appmode.h)
Before a program runs, the whole file is read, so AerOS knows every part of itself the program
can use: built-ins are only reached by name, every name is in the source, and there is no eval.
From the names it works out the program's profile (graphics, sound, video, files, network) and
puts away what the program won't use. At the moment that is the network card's buffers, about
82 KB, as everything else is only in memory while it is used anyway. It happens for every
program, however new, and a part that was put away starts again the next time it is used.
`appmode()` gives back `[parts used, KB put away, KB free]`, e.g.
`[["graphics", "files"], 82, 257990]`, and puts away again anything started since. At the
prompt it puts away everything it can. `MEM` shows what app mode did for the last program.

**Network** (sealed messages, to and from computers the firewall allows: see NETWORK.md)
`nsend(host, port, text)` true if it was sent (host is an address, or a name a rule was made
for) · `nrecv(port, ms)` wait up to ms milliseconds: the message, or nil ·
`httpget(url)` fetch a web page (http:// only) from a computer a web rule (`FW WEB`) allows: its
text if the status was 2xx, or nil

**TCP** (plain connections both ways: a server, or a client for any protocol; see NETWORK.md,
"Listening, and a web server", and the `webserve` package, `packages/webserve.aer`, for a
whole web server)
`tcplisten(port)` let other computers connect to AerOS's port (`FW SERVE <port>` must allow it):
a number, or nil · `tcpaccept(listener, ms)` wait for a visitor: a connection, or nil ·
`tcpconnect(host, port)` connect out (`FW WEB` must allow it): a connection, or nil ·
`tcprecv(c, ms)` what has arrived, `""` once they have finished, nil if nothing came ·
`tcpsend(c, data)` send a string or bytes · `tcpclose(c)` · `tcpunlisten(listener)`

**Trust** (`src/lang/bi_trust.c`)
`verify(data, signature, key)` was data (text or bytes) signed by the private key that goes with
key? signature (128 hex digits) and key (64) are hex; anything malformed is false ·
`packagekey()` the public key packages are checked against (`src/sys/package_key.h`, "" until set) ·
`scan(source)` what a program could do, read without running it: `[abilities, warnings]`, two lists
of text

**Encrypted files** (AES-256 with a password, the file keeps its name)
`enc(path, password)` · `dec(path, password)` (both true if it worked) ·
`isenc(path)` · `rdenc(path, password)` the contents, without changing the file
(nil if the password is wrong)

**System**
`sleep(ms)` · `time()` (milliseconds since boot) · `random(n)` (0 to n - 1) · `beep(hz)` ·
`play(path)` play a WAV or MP3 file and wait for it to finish (false if it can't be played) ·
`video(path)` play an .mpg video full screen with its sound, until it ends or Esc is pressed
(Space pauses, Left / Right skip 5 seconds); true if it played. It takes the whole screen and
gives it back to the terminal, so a program drawing graphics calls `gopen()` again after it
(see [VIDEO.md](VIDEO.md)) · `exit()` ·
`queue(line)` add a program to the queue the `GO` command runs, e.g. `queue("hello.aer a b")`
or `queue("s:some text")`; returns how many are queued (-1 if the process stack is full) ·
`launch(app, back)` when the program ends, run an installed app (`"maze3d"`, or a path such as
`"/apps/maze3d.aer"`: a program in `/bin/`, `/bin/games/` or `/apps/`), then start the program
again with `back` (text, optional) as its `args`; true if that will happen. Only one program
runs in front at a time, so this is how the desktop opens apps. The app gets nothing after its
name, and the program's `back` doesn't count as typed: files named in either are asked about
as usual. It won't start an app in a folder the program could change without asking (one it
made, or was told yes to), so a program can't write a program and quietly run it. After a text
app AerOS waits for a key before the program comes back ·
`power(what)` turn the computer off (`"off"`, as `OFF` does) or restart it (`"restart"`, as
`RBT`), after AerOS asks you itself (a box over graphics); false if you said no, otherwise it
never comes back. Save first: there is no going back once you say yes ·
`sysinfo()` everything AerOS knows about the computer, as a list of `[group, name, value]`
rows, e.g. `["Processor", "Speed", "about 2400 MHz"]` (`/bin/sysinfo.aer` shows them) ·
`telemetry()` how busy AerOS is right now, for a live display (the desktop's system monitor
asks once a second). Unlike `sysinfo()` it is quick: it only reads counters AerOS keeps anyway,
never waits, never touches a disk and never starts a network card app mode put away. It gives
`[ms, idle ms, threads, memory, disk cache, network]`, e.g.
`[6294, 3353, [["main", "running"], ["disk writer", "sleeping"]], [4286, 257489], [1, 4096, 1618, 11, 0], [true, false, true, "10.0.2.15", 0, 0, 0, 0]]`:
`ms` is milliseconds since AerOS started and `idle ms` how many of them the processor had
nothing to do (stopped with `hlt` until the next interrupt: waiting for a key, `sleep()`,
`frame()`; `clck_idle_ms` in `src/sys/sys_clck.h`), so asked twice, the share of the time
between that wasn't idle is the CPU load; `threads` is `[name, state]` for each thread
(`"running"`, `"ready"`, `"sleeping"`, `"waiting"`: see THREADS.md); `memory` is
`[KB of the kernel heap used, KB it has]`; `disk cache` is `[KB kept, KB it can keep,
sectors found in it, sectors read from the drive, KB changed and not written yet]`
(DISK_CACHE.md); `network` is `[is there a card, is it running, is the link up, AerOS's
address, sealed messages sent, received, packets blocked, firewall rules]` (NETWORK.md: a card
app mode put away isn't running, but its link is still known) ·
`disks()` the hard disks there are, as `[number, model, megabytes, what is on it]` ·
`install(number)` erase that disk and install AerOS on it: only in demo mode, and only after
AerOS has asked you itself (see [installing](INSTALLING.md)) ·
`osbuild()` this AerOS's build number, `"20261204183000"` · `osversion()` its version,
`"0.01 ALPHA"` · `osupdatable()` can it be updated here (installed on a disk)? `false` after
saying why not (`osupdatable(false)`: without saying) · `osupdate(kernel, signature)` put a new AerOS on the disk, to start next time:
only one signed by AerOS's makers and newer than the one there, and only after AerOS has asked
you itself (see [updating](UPDATING.md))

`args` is the list of words typed after the program's name.

**Animation** (the same bars and spinners AerOS itself uses, see `src/sys/ui.h`)
`progress(x, y, width, done, total, colour)` a bar like `[#####.....]  50%` (the bar is
`width` cells, plus 8 for the brackets and the percentage) ·
`spinner(x, y, style, colour)` the spinner's frame for this moment, style 0 to 4 (line, arrows,
pulse, dots, bounce); returns how many cells it took ·
`frame(fps)` wait until it's time for the next frame, so a loop runs at a steady speed however
long the drawing took. The colours can be left out.

`big(x, y, text, colour, background)` text in big letters, like the splash screen's
"aerOS": each letter is 8 columns wide and 7 rows tall (10 fit across the screen), drawn with
half block characters, so it works on any screen. The colour (WHITE) and background (BLACK)
can be left out; returns how many columns wide it is, e.g. to centre it:
`big((80 - len(title) * 8) / 2, 2, title, ORANGE)`

```
while not haskey() do
    progress(2, 10, 40, time() / 50 % 101, 100)
    spinner(2, 12, 4, LIGHT_CYAN)
    frame(20)
end
```

The `anim` package (`pkg install anim`, `packages/anim.aer` in the repository) shows them all.

**Graphics**
Call `gopen()` to take over the screen (it returns false if there is no graphics screen) and
`gclose()` to give it back to the terminal, which also happens on its own when the program ends.
Colours are numbers like `0xFF7F00`, or `rgb(255, 127, 0)`. Drawing off the edge of the screen is fine.

`gw()` · `gh()` · `gclear(colour)` · `pixel(x, y, colour)` ·
`rect(x, y, w, h, colour)` filled · `line(x1, y1, x2, y2, colour)` ·
`text(x, y, text, colour, size)` (size 1 is 8 x 16 pixels per character) ·
`image(bytes, x, y, w, h)` draw a picture, scaled to fit `w` x `h` if given (up to 16384 x 16384,
so it can be far bigger than the screen; only the part on screen is drawn): BMP (8, 24 or 32
bit), PNG, GIF (its first picture) or JPEG (baseline or progressive). See-through parts of a PNG
or GIF show what was drawn there before · `imgsz(bytes)` its `[width, height]`, or nil if it
isn't one of those. Both take the picture as bytes from `rdb`, or as fetched by `httpget` (the
`browser` package shows web pictures this way). A PNG, GIF or JPEG is decoded the first time
it is drawn and kept, so drawing the same one again (every frame of a game) is quick

Two more draw a picture as a "texture", for ray casting games like `maze3d`, where a per-pixel
loop in AerLang would be far too slow. Both repeat the picture past its edges (so it tiles),
darken it by `light` (1 is the picture as it is, 0 black, so far walls can be dark), take
positions in the picture's pixels (decimals allowed), and give false if it isn't a picture.
See-through parts are drawn solid, except by `imgcol`'s `see_through` ·
`imgcol(bytes, x, y, w, h, u0, u1, light, see_through)` a strip of wall: `w` x `h` at `x`, `y`,
showing the picture's columns from `u0` at its left to `u1` at its right, each its whole height
stretched to `h` (taller than the screen is fine). With `see_through` true it is a strip of a
sprite: the picture's see-through pixels are left out, so what is behind shows (a 32 bit BMP's
4th byte counts as how solid a pixel is: under 128 is see-through; a 24 bit BMP is solid), and
it is lit like a wall, so a monster in the dark only shows in your flashlight's beam · `imgrow(bytes, x, y, w, h, u0, v0, u1, v1, light)` a
band of floor or ceiling: `w` x `h` at `x`, `y`, every row the picture read along the straight
line from (`u0`, `v0`) at the left to (`u1`, `v1`) at the right ·
`imglight(x, y, radius, ambient)` a spotlight, like a flashlight, on everything `imgcol` and
`imgrow` draw from then on: they are lit only inside a circle round `x`, `y` on the screen (fully
out to a third of the radius, then fading) and outside it only by `ambient` (0 to 1, 0 if left
out: pitch black), the faint light of a room you can just make things out in, or for a moment
the whole room, in a flash of lightning. `imglight()` turns it off (it is off
when a program starts); other drawing (`rect`, `text`, `image`) is never dimmed by it, so
glowing things can be drawn in the dark. Use BMPs for these (32 bit are quickest): they are drawn straight from their bytes, while only the last PNG, GIF or JPEG
decoded is kept, so drawing several in turn decodes them again and again (in `maze3d`, 3
frames a second with PNGs against 25 with BMPs). How they work is in
`src/drivers/video/gfx.c`

```
gopen()
let picture = rdb("/apps/viewer/aeros.bmp")      # a picture the viewer package comes with
image(picture, 0, 0)
text(10, 320, "Press a key", 0xFFFFFF, 2)
key()
```

Two more change what is already drawn, for effects over a whole picture:
`gtint(x, y, w, h, colour, amount)` mixes every pixel of that area with `colour`, `amount` of the
way (0 to 1): a little black more each frame fades out, a moment of red is a hurt ·
`gnoise(x, y, w, h, amount)` makes every pixel lighter or darker by a random amount up to
`amount` (0 to 255), like the grain of a picture seen in the dark, or static; it is different
each time, so it seethes. Both work on every pixel, so on a slow computer use them on part of
the screen, or now and then. How they work is in `src/drivers/video/gfx.c`.

**For games** (smooth movement, keys held down, sounds that overlap)
`gbuf(on)` with `true`, draw into a back buffer (a copy of the screen in memory) instead of
the screen, so nothing shows until `gshow()` · `gshow()` put the finished frame on the screen
in one go, so it never flickers · `keydown(key)` is the key held down right now? A character
(`"a"`, `" "`) or `KEY_LEFT`, `KEY_SPACE`, `KEY_SHIFT`, `KEY_CTRL`, `KEY_ALT`... It doesn't wait
(unlike `key()`) and any number of keys can be down at once ·
`sload(path)` load a WAV or MP3 into memory, ready to play (nil if it can't) ·
`splay(sound, loop, volume, pan)` start it and carry on straight away; `loop` plays it round and
round, `volume` is 0 to 100, and `pan` puts it on the left (-100) or the right (100), or
anywhere between (0, the middle, if left out). Up to 8 sounds play at once, mixed together.
Returns a number for:
`svol(n, volume, pan)` change its volume and pan while it plays, to fade it or to move it round
you as whatever makes it moves (or as you turn) · `sstop(n)` stop it (`sstop()` stops every
sound) · `isply(n)` is it still playing?
Sounds stop when the program ends.

Two ways to read keys, for two jobs: `keydown()` for things that last while a key is held
(moving), and `haskey()` / `key()` for single presses (jumping, firing). `keydown()` only
sees the moment it is asked, so a quick tap between two frames can slip past it, while every
press waits for `key()` until it is read.

**The mouse** (a program has it to itself: the terminal only uses it for its pointer while the
prompt waits, see `src/sys/terminal/pointer.c`)
`mouse()` where the pointer is and which buttons are held, as `[x, y, buttons]`: pixels from
the top left of the screen, starting in the middle, and `buttons` adds up `MOUSE_LEFT` (1),
`MOUSE_RIGHT` (2) and `MOUSE_MIDDLE` (4). nil if there is no mouse · `mousemove()` how far the
mouse has moved since the last `mousemove()`, as `[dx, dy]` (right and down are positive). It
never stops at the edge of the screen, so it is the one for turning a camera: `[0, 0]` with no
mouse, so a game still works with the keys alone · `mousedown(button)` is that button held?
AerOS draws no pointer: a program draws its own, e.g. `rect(m[0], m[1], 4, 4, 0xFFFFFF)`.

The mouse switches on the first time a program asks about it and off when the program ends.
It is a PS/2 mouse (`src/drivers/input/mouse.c`). In QEMU, click inside the window so it takes
the mouse, and press Ctrl+Alt+G to let go. On a real computer a USB mouse usually works too, as
long as the computer's firmware offers "legacy USB" (it then looks like a PS/2 mouse).

```
gopen()
gbuf(true)
let jump = sload("/sys/startup.wav")
let x = 100
while not keydown(KEY_ESCAPE) do
    if keydown(KEY_LEFT) then
        x = x - 4
    end
    if keydown(KEY_RIGHT) then
        x = x + 4
    end
    if haskey() and key() == ord(" ") then
        splay(jump)
    end
    gclear(0)
    rect(x, 200, 40, 40, 0xFF7F00)
    gshow()
    frame(60)                # 60 frames a second
end
```

The `move` package (`packages/move.aer`) is a small example of all of these. How they work:
the back buffer is in
`src/drivers/video/gfx.c`, held keys in `src/drivers/input/IO.c`, and the mixer in
`src/sys/mixer.c`, which feeds the sound card from the timer interrupt so sound keeps playing
smoothly whatever the program is doing.

The `maze3d` package (`pkg install maze3d`) goes further: a first-person 3D maze, drawn with
ray casting the way early 3D games were. For each strip of the screen it sends a ray across a
grid of squares until it hits a wall, and draws that wall shorter the further away it is. The
crystals are sprites, placed with the same maths backwards and hidden behind nearer walls. So
are the ghosts that haunt it, which find their way to you along a step map of the maze (how
many steps every square is from yours, counted outwards from you): one that touches you costs
a life, and your light (Space or a click) sends one away for a while. Its menu chooses the
hand-drawn maze or a new one made up for each game (up to 55 x 55 squares, dug out by a
"recursive backtracker"), and how many crystals, ghosts and lives there are. The map in the
corner is a BMP it builds in `bytes(n)` once for each maze and draws with `image()`, which
is far quicker than a rectangle for every wall of a big maze on every frame. Its music and
sound effects are WAV files it comes with, loaded once with `sload` and started with `splay`,
so the quiet looped music plays on under footsteps, crystals and ghosts (the menu turns the
music, or all sound, off; with no sound card the important sounds are `beep`s). They are
made from plain waves, the way old game chips made them, by `tools/make_maze3d_sounds.py`,
which can be changed and run again. It looks like a dungeon: stone, brick and mossy walls, a
flagstone floor and a cobbled ceiling, pitch black but for the beam of your flashlight
(`imglight`), in which the ghosts and crystals glow. They are 64 x 64 BMPs worked out pixel
by pixel (blocks, bevels, noise and a Voronoi pattern for the cobbles) by
`tools/make_maze3d_textures.py`, and drawn with `imgcol` (each wall strip, picked by where its
ray hit the wall) and `imgrow` (the floor and ceiling a band of rows at a time, each row being
all one distance away), darker the further away they are. The menu's LOOK can instead light
it all round with a LANTERN, or be WALLS ONLY or PLAIN, the old flat colours, for a slow
computer (in QEMU, the flashlight runs at about 21 frames a second on the classic maze and
17 on the huge one, and PLAIN at about 30 and 21). It uses
decimals, `sin` / `cos`, `keydown`, `mousemove` (for looking around), `gbuf` and `frame`,
and its comments explain each step, so it is meant to be read and changed: draw your own maze
in `MAP`, add wall colours, or widen `STRIP_WIDTH` for more speed on a slow computer.

The `knocks` package (Four Knocks, `pkg install knocks`) builds a horror game on Maze 3D's ray
caster. Its levels are letterboxed like a film, the thing that hunts you and the furniture are
sprites drawn with `imgcol(..., true)`, lit only by your flashlight, and every sound has a
place: `splay`'s pan and `svol` put the thing's footsteps on your left or right as you turn,
quieter the further away it is, counted along the corridors with a step map so you hear it
round corners. It hunts by ear: each frame you make a noise that carries a number of steps
(none standing still, a few walking, more running), and if it is that close, it hears you.
Lightning is `imglight`'s ambient light for a moment, the static round the thing is `gnoise`,
and fades and the red of the alarm are `gtint`. Its pictures are 3D models rendered from five
sides by `tools/make_knocks_sprites.py` (ray marching "signed distance functions" with numpy),
and its sounds, music and voices are made by `tools/make_knocks_sounds.py`. "knocks demo"
plays it by itself, start to end.

The `viewer` package is a complete app built from these: a picture picker and viewer that
zooms (`+` / `-`, `0` to fit) by drawing the picture bigger than the screen and moving it
around. (The games, the viewer and the other packages are in the repository's `packages/`
folder, to read without installing them: see [packages](PACKAGES.md#the-packages).)
`/bin/read.aer` is a bigger one: the Markdown / HTML reader behind the `DOC` command, with its
own parsers, word wrapping and link navigation, all in AerLang.
