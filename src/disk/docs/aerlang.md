# AerLang

AerLang is the programming language built into AerOS. A program is a text file ending in
`.aer`. Every app in `/bin/`, including this reader, is an AerLang program you can open with
`read` and change with `edt`.

## Running programs

| Type | To |
| --- | --- |
| `hello` | run `hello.aer` from here, or from `/bin/` |
| `hello.aer a b` | the same, with `args` set to `["a", "b"]` |
| `2 + 2`, `let x = 5` | right at the terminal's prompt: see [The prompt speaks AerLang](shell.md) |
| `aer` | a prompt where every line is code, `exit` to leave |
| `edt app.aer` then Ctrl+R | write a program and run it |

**Ctrl+C** stops a program that is stuck.

Programs from before AerLang got its name end in `.ren`. They still run if you type the
extension too: `old.ren`.

## The language

```
# a comment runs to the end of the line
let name = "AerOS"              # make a variable
name = name + "!"               # change it
print("Hello", name)            # Hello AerOS!

if len(name) > 3 then
    print("long")
elif len(name) == 3 then
    print("three")
else
    print("short")
end

for i = 1 to 10 do              # add "step 2" to count in twos
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
print(area(3, 4))               # 12
```

- A statement ends at the end of its line. Short blocks fit on one line:
  `if x then print(x) end`
- Variables are made with `let`. Using a name that was never made is an error, which
  catches typing mistakes.
- Variables made with `let` inside a function belong to it. Everything else is global.
- Functions are made at the top level and can be used before the line that makes them.

## Values

| Type | Examples | Notes |
| --- | --- | --- |
| number | `42`, `-7`, `0xFF` | whole numbers, `/` drops the remainder |
| decimal | `3.14`, `-0.5`, `2.5e3` | `7 / 2.0` is `3.5`: a decimal on either side gives a decimal |
| string | `"hi\n"` | `s[0]` is the first character |
| list | `[1, "two", [3]]` | `l[0] = 5` changes an item |
| bytes | `bytes(100)` | raw bytes, e.g. from `rdb` |
| bool | `true`, `false` | |
| nil | `nil` | nothing |

`nil`, `false` and `0` count as false. `and` / `or` give back one of their sides, so
`input("Name? ") or "friend"` works. `+` joins strings: `"Score: " + 10`.

Two whole numbers give a whole answer, as always (`7 / 2` is `3`). Decimals print with up to
six places (`3.141593`), and a built-in that wants a whole number, like a screen position,
rounds a decimal down, so a game can keep positions as decimals: `rect(x, y, 10, 10, c)`.

**Maths:** `sqrt(x)`, `sin(a)`, `cos(a)` (angles in radians), `atan2(y, x)`, `floor(x)`,
`round(x)`, `abs(x)`, `float(x)` (`float("2.5")`), and `PI`. A step of `speed` in direction
`a` is `x = x + cos(a) * speed` and `y = y + sin(a) * speed`.

## Built-in functions

**Screen and keys:** `print(...)`, `write(...)` (no new line), `input(prompt)`,
`inpw(prompt)` (shows `*`), `cls()`, `color(fg, bg)`, `at(x, y)`, `key()` (waits,
gives the key's code), `haskey()`.

**The clipboard:** `clip()` gives the text on it ("" if none), and `setclip(text)` puts text
on it. It is the same clipboard the mouse copies to and pastes from at the prompt (see
[Getting started](start.md)).

Colours: `BLACK`, `DARK_BLUE`, `GREEN`, `CYAN`, `RED`, `PURPLE`, `ORANGE` (the logo
orange, also called `BROWN`), `BRIGHT`, `DARK_GRAY`, `LIGHT_BLUE`, `LIGHT_GREEN`,
`LIGHT_CYAN`, `LIGHT_RED`, `LIGHT_PURPLE`, `YELLOW`, `WHITE`. Keys: `KEY_UP`,
`KEY_DOWN`, `KEY_LEFT`, `KEY_RIGHT`, `KEY_HOME`, `KEY_END`, `KEY_PGUP`, `KEY_PGDN`,
`KEY_DELETE`, `KEY_ENTER`, `KEY_BACKSPACE`, `KEY_TAB`, `KEY_ESCAPE`.

**Text:** `len(x)`, `str(x)`, `int("42")` (nil if not a number; `"08"` is 8, `"0x1F"` is hex), `chr(65)`, `ord("A")`,
`sub(s, start, count)`, `find(s, part)` (-1 if missing), `upper(s)`, `lower(s)`,
`split(s, sep)`, `join(list, sep)`.

**Lists:** `list(n, fill)`, `push(l, x)`, `pop(l)`, `insert(l, i, x)`, `remove(l, i)`,
`bytes(n)`.

**Files:** `rdf(path)` (nil if missing), `rdb(path)`, `wrf(path, text)` (text or bytes),
`apf(path, text)`, `isf(path)`, `delf(path)`, `la(dir)`,
`cwd()`. **Folders:** `mkd(path)` makes one (the program may then change files in it without
asking, until it ends), `deld(path)` deletes one and the files in it. A program [asks you first](perms.md) before it reads your files or changes any
file, and if you say no the function fails as if the file wasn't there. `rdf("notes.txt@2")`
reads an [old version](versions.md) of a file.

**Databases:** `dbopen(path)` (or `dbopen(path, "read")`, only to read), `dbrun(db, sql, values)`, `dbquery(db, sql, values)` (a list of
rows) and `dbclose(db)` keep tables of rows in a file, in SQL: see [Databases](database.md).

To change a file a piece at a time (a database's pages, say) rather than all of it:
`rdat(path, offset, count)` reads `count` bytes from byte `offset`, `wrat(path, offset, data)`
writes bytes or text there, in place, and `trunc(path, size)` makes the file that long. Writing
past the end makes it longer, with zeros in any gap. These keep no old versions.

The names are built like the terminal's commands: `rd` read, `wr` write, `ap` append,
`del` delete, `is` "is there one?", then `f` file or `b` bytes. So `rdf` reads a file and
`isf` asks if a file is there. `enc` / `dec` encrypt and decrypt, and `g` means graphics.

**Encrypted files:** `enc(path, password)`, `dec(path, password)`,
`isenc(path)`, `rdenc(path, password)` (nil if the password is wrong).

**System:** `sleep(ms)`, `time()` (ms since start), `random(n)` (0 to n - 1), `beep(hz)`,
`play(path)`, `video(path)` (an .mpg, full screen: see [Video](video.md)), `logadd(message)`
(adds to [AerOS's log](logs.md)), `logclear()` (clears it, after asking you),
`setlogging(on)` and `islogging()` (turn it off or on, after asking), `exit()`.
`disks()` lists the hard disks, and `install(number)` erases one and installs AerOS on it
(only in demo mode, after asking you). `osbuild()` and `osversion()` say which AerOS this is,
and `osupdate(kernel, signature)` puts a newer one on an installed disk, to start next time:
only one signed by AerOS's makers, after asking you (`osupdatable()` says if it can).
`args` is the list of words typed after the program's name.
`sysinfo()` gives everything AerOS knows about the computer as a list of
`[group, name, value]`, e.g. `["Processor", "Speed", "about 2400 MHz"]`.
`telemetry()` says how busy AerOS is right now, quickly enough to ask every second (the
desktop's system monitor does): `[ms, idle ms, threads, memory, disk cache, network]`, e.g.
`[6294, 3353, [["main", "running"], ["disk writer", "sleeping"]], [4286, 257489],
[1, 4096, 1618, 11, 0], [true, false, true, "10.0.2.15", 0, 0, 0, 0]]`. Ask twice: the
processor was busy for the time between, less the idle time between. Memory is
`[KB used, KB there]`, the disk cache `[KB kept, KB it can keep, reads found in it, reads
from the disk, KB waiting to be written]`, and the network `[card?, running?, link up?,
address, sent, received, blocked, firewall rules]`.
`queue("hello.aer")` adds a program (with its arguments) to the queue that `go` runs, and
gives back how many are queued. `launch("maze3d", "back")` runs an installed app (in `/bin/`
or `/apps/`) when the program ends, then starts the program again with `back` as its `args`:
the desktop opens apps this way. `power("off")` and `power("restart")` turn the computer off
or restart it, after AerOS asks you. At the prompt only, `cmd("la")` runs a terminal command and
gives back what it printed, as a list of lines.

**App mode:** before a program runs, AerOS reads all of it, so it knows every part of AerOS
the program can use, and puts away the parts it won't, to leave their memory for the
program. It happens by itself, for every program, even one saved a second ago. `appmode()`
gives back what it did: `[["graphics", "files"], 82, 257990]` is the parts the program uses,
the KB put away for it, and the KB free. It also puts away again anything that has started
since. A part that was put away starts again by itself the next time something uses it. `mem`
shows what app mode did for the last program.

**Network:** `nsend(host, port, text)` sends a sealed message to a computer the firewall
allows (true if it was sent), and `nrecv(port, ms)` waits up to `ms` milliseconds for one:
the message, or nil. `httpget(url)` fetches a web page from a computer a web rule allows
(`fw web`): its text, or nil. `fwallowed(host, port)` says whether a site is allowed already,
and `fwallow(host, port)` asks you (AerOS asks, in its own box, Y or N) whether to allow it, and
only makes the web rule if you say yes. See [The network](network.md).

**Trust:** `verify(data, signature, key)` checks a signature (Ed25519, as hex) made with the
private key that goes with `key`: true only if `data` is exactly what was signed. `packagekey()`
is the key AerOS trusts packages from. `scan(source)` reads a program's text without running it,
like [the SCAN command](perms.md): a list of what it can do, and a list of anything that looks
like a trick.

When a name isn't known, the error suggests the closest one that is:
`unknown name 'prnt' (did you mean print?)`. `why` after an error shows the lines around it,
and `why gopen` (or any built-in's name) explains a function from the C code behind it.

**Animation:** `progress(x, y, width, done, total, colour)` draws a bar like
`[#####.....]  50%`, `spinner(x, y, style, colour)` draws a spinner as it should look right
now (styles 0 to 4: line, arrows, pulse, dots, bounce), and `frame(fps)` waits for the next
frame, so a loop runs at a steady speed. The colour can be left out.

**Big text:** `big(x, y, text, colour, background)` writes text in big letters like the
splash screen's "aerOS": each letter is 8 columns wide and 7 rows tall, so 10 fit across the
screen. It gives back how many columns wide it is, handy for centring:

```
let title = "Hello"
big((80 - len(title) * 8) / 2, 2, title, ORANGE)
```

```
let done = 0
while done <= 100 do
    progress(2, 10, 40, done, 100)
    spinner(2, 12, 4)
    done = done + 1
    frame(20)                   # 20 frames a second
end
```

**Graphics:** `gopen()` takes over the screen and `gclose()` gives it back.
Colours are numbers like `0xFF7F00` or `rgb(255, 127, 0)`.
`gw()`, `gh()`, `gclear(c)`, `pixel(x, y, c)`, `rect(x, y, w, h, c)`,
`line(x1, y1, x2, y2, c)`, `text(x, y, s, c, size)`, `image(bytes, x, y, w, h)` (a BMP,
PNG, GIF or JPEG picture, from `rdb` or `httpget`), `imgsz(bytes)`. `w` x `h` can be bigger than the screen and `x`, `y`
negative: only the part on the screen is drawn, which is how the viewer zooms in.
For ray casting games, `imgcol(bytes, x, y, w, h, u0, u1, light, see_through)` stretches a
picture over a w x h strip of wall (its columns from `u0` to `u1`, its whole height; with
`see_through` true its see-through pixels are left out, for a strip of a sprite), and
`imgrow(bytes, x, y, w, h, u0, v0, u1, v1, light)` fills a band of floor, each row read along
the line from (`u0`, `v0`) to (`u1`, `v1`). Both repeat the picture past its edges and darken
it by `light` (1 as it is, 0 black). `imglight(x, y, radius, ambient)` then lights only a
circle round `x`, `y`, like a flashlight, and outside it only `ambient` (0 to 1, 0 if left out:
black; `imglight()` turns it off). Over what is drawn, `gtint(x, y, w, h, c, amount)` mixes in
a colour (0 to 1 of the way: to fade, or flash red) and `gnoise(x, y, w, h, amount)` adds
grain, each pixel up to `amount` (0 to 255) lighter or darker. Use BMPs for these: they are drawn straight from their
bytes, while a PNG, GIF or JPEG is decoded again whenever a different one was drawn last.

```
gopen()
image(rdb("/apps/viewer/aeros.bmp"), 0, 0)   # after pkg install viewer
text(10, 320, "Press a key", 0xFFFFFF, 2)
key()
```

**For games:** `gbuf(true)` draws into a back buffer out of sight and `gshow()` puts
each finished frame on the screen, so moving things don't flicker. `keydown(k)` says if a
key is held right now (`"a"`, `KEY_LEFT`, `KEY_SPACE`, `KEY_SHIFT`...), without waiting.
`sload(path)` loads a WAV or MP3, and `splay(sound, loop, volume, pan)` starts it and carries
on at once (pan -100 is the left speaker, 100 the right): up to 8 sounds play together.
`svol(n, volume, pan)` changes one as it plays, `sstop(n)` stops one, `isply(n)` asks if it
still plays. Use `keydown` for things that last (moving) and `haskey()` / `key()` for single
presses (jumping): every press waits there until it is read, so a quick tap is never missed.
The `move` package (`pkg install move`) uses them all.

**The mouse:** `mouse()` gives `[x, y, buttons]`, where the pointer is in pixels and which
buttons are held (`MOUSE_LEFT` 1, `MOUSE_RIGHT` 2, `MOUSE_MIDDLE` 4 added up), or nil if there
is no mouse. `mousemove()` gives `[dx, dy]`, how far it moved since the last time: it never
stops at the edge of the screen, so it is the one for turning a camera, and it gives `[0, 0]`
with no mouse. `mousedown(MOUSE_LEFT)` asks if a button is held. AerOS draws no pointer for a
program, so it draws its own. The terminal shows one while its prompt waits, but a program has
the mouse to itself: it switches on when the program asks about it and off when it ends. In
QEMU, click the window first (Ctrl+Alt+G lets go). The `maze3d` package looks around with it.

```
gopen()
gbuf(true)
let x = 100
while not keydown(KEY_ESCAPE) do
    if keydown(KEY_RIGHT) then
        x = x + 4
    end
    gclear(0)
    rect(x, 100, 40, 40, 0xFF7F00)
    gshow()
    frame(60)
end
```

## Programs to learn from

| File | Shows |
| --- | --- |
| `/bin/hello.aer` | input, functions, lists, loops |
| `/bin/read.aer` | this reader: text handling, a whole app |
| `/bin/sysinfo.aer` | system information: lists, layout and scrolling |
| `/bin/pkg.aer` | installing packages: fetching, checking signatures, files |

And these [packages](commands.md), once installed (`pkg install guess`), are in `/apps/`:

| File | Shows |
| --- | --- |
| `/apps/guess.aer` | random numbers and a game loop |
| `/apps/viewer.aer` | graphics, pictures and a menu |
| `/apps/move.aer` | smooth movement, held keys and mixed sounds |
| `/apps/snake.aer` | a whole game: graphics, a back buffer, held keys, lists, and high scores in a database |
| `/apps/maze3d.aer` | a 3D maze drawn with ray casting: decimals, sin / cos, textured stone walls and floors lit by a flashlight (imgcol / imgrow / imglight), sprites, mouse look, ghosts that find their way to you, made-up mazes, and sound effects and music mixed with sload / splay |
| `/apps/knocks.aer` | a horror game on the same ray caster: sprites lit by the flashlight (imgcol's see_through), sounds placed left and right (splay's pan, svol), lightning (imglight's ambient), static and fades (gnoise, gtint), and a monster that hunts by ear along a step map |
| `/apps/lantern.aer` | a tile and sprite game: one-row sheets drawn a tile at a time with imgcol (a picture's columns n * 16 to n * 16 + 16), lit per tile by a lantern and fires, a world made from a seed with its own random numbers, value noise and Dijkstra's shortest paths, a story made from parts, dialogue boxes, saving, and a demo that plays itself along a step map |
| `/apps/anim.aer` | spinners, progress bars and moving text |
| `/apps/webserve.aer` | a web server: TCP, and serving files |
