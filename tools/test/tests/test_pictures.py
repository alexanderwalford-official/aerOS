"""Pictures: PNG, GIF and JPEG, decoded by AerOS (src/drivers/video/png.c, gif.c, jpeg.c and
src/lib/inflate.c) and drawn with image(), checked pixel by pixel against Pillow's decoding of
the same files. Then damaged ones, which must be turned down (or drawn partly) without harm, as
pictures come from the web.

Most of the pictures are made with Pillow. A few PNGs are written by hand here (png_by_hand) to
be sure of the parts Pillow doesn't choose: every filter, 16 bit RGB, 2 bit grey, interlacing,
and a see-through RGB colour."""

import io
import os
import random
import struct
import time
import zlib

from aeros import Skip

try:
    from PIL import Image
except ImportError:
    Image = None

BACKGROUND = (40, 80, 120)    # gclear's colour: see-through pixels must show it
CELL_W, CELL_H = 128, 96      # each picture gets a cell this big, in rows across the screen
COLUMNS = 7
MARKER = (255, 0, 255)        # drawn last, in the bottom right corner, when everything is drawn


def scene(w, h, seed):
    """An RGB picture with smooth parts, sharp edges and noise, so every kind of picture has
    something to squeeze (and JPEG some detail to lose)."""
    rnd = random.Random(seed)
    img = Image.new('RGB', (w, h))
    pixels = []
    for y in range(h):
        for x in range(w):
            r = x * 255 // max(1, w - 1)
            g = y * 255 // max(1, h - 1)
            b = (x * 7 + y * 3) % 256
            if (x // 12 + y // 12) % 2 == 0:   # a checkerboard of sharp edges
                r, b = b, r
            if (x - w // 2) ** 2 + (y - h // 2) ** 2 < (min(w, h) // 3) ** 2:   # a disc
                r, g, b = 250 - g // 2, 230, 40 + r // 4
            n = rnd.randint(-12, 12)
            pixels.append(tuple(max(0, min(255, c + n)) for c in (r, g, b)))
    img.putdata(pixels)
    return img


def saved(img, format, **options):
    """A Pillow picture saved to bytes."""
    out = io.BytesIO()
    img.save(out, format, **options)
    return out.getvalue()


def chunk(kind, body):
    """One PNG chunk: its length, type, body and checksum."""
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF)


def png_by_hand(w, h, colour_type, depth, sample, interlace=False, plte=None, trns=None):
    """A PNG written here rather than by Pillow: sample(x, y) gives a pixel's samples (at the
    file's depth). Rows use every filter in turn (0 to 4), and interlace=True stores it in Adam7's
    7 passes."""
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    bits = channels * depth
    step = max(1, bits // 8)

    def raw_row(xs, y):
        if depth >= 8:
            out = bytearray()
            for x in xs:
                for s in sample(x, y):
                    out += s.to_bytes(depth // 8, 'big')
            return bytes(out)
        # samples smaller than a byte, packed from the top bit down
        value, count, out = 0, 0, bytearray()
        for x in xs:
            for s in sample(x, y):
                value = (value << depth) | s
                count += depth
                if count == 8:
                    out.append(value)
                    value, count = 0, 0
        if count:
            out.append(value << (8 - count))
        return bytes(out)

    def filtered(rows):
        out, above, n = bytearray(), None, 0
        for row in rows:
            kind = n % 5
            n += 1
            prior = above or bytes(len(row))
            line = bytearray()
            for i, v in enumerate(row):
                a = row[i - step] if i >= step else 0
                b = prior[i]
                c = prior[i - step] if i >= step else 0
                if kind == 0:
                    p = 0
                elif kind == 1:
                    p = a
                elif kind == 2:
                    p = b
                elif kind == 3:
                    p = (a + b) // 2
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    p = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line.append((v - p) & 255)
            out += bytes([kind]) + line
            above = row
        return bytes(out)

    if interlace:
        passes = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
        data = b''
        for x0, y0, dx, dy in passes:
            xs, ys = list(range(x0, w, dx)), list(range(y0, h, dy))
            if xs and ys:
                data += filtered([raw_row(xs, y) for y in ys])
    else:
        data = filtered([raw_row(range(w), y) for y in range(h)])
    out = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, depth, colour_type, 0, 0, int(interlace)))
    if plte:
        out += chunk(b'PLTE', plte)
    if trns:
        out += chunk(b'tRNS', trns)
    # a big stream in more than one IDAT, as real PNGs often have
    squeezed = zlib.compress(data, 9)
    half = len(squeezed) // 2
    return out + chunk(b'IDAT', squeezed[:half]) + chunk(b'IDAT', squeezed[half:]) + chunk(b'IEND', b'')


def jpeg_unsmoothed(data, h, v):
    """What a JPEG with smaller colour planes (h x v times smaller) should look like when they
    are stretched the way AerOS does, each colour sample used for h x v pixels. Pillow smooths
    them as it stretches them, so this asks libjpeg for the planes as they are stored instead:
    decoded at 1 / h x 1 / v the size, the colour planes need no stretching, while the brightness
    plane comes from a full size decode. Then YCbCr to RGB, as the JPEG standard (JFIF) says."""
    full = Image.open(io.BytesIO(data))
    full.draft('YCbCr', full.size)
    luma = full.getchannel(0)
    small = Image.open(io.BytesIO(data))
    # (Pillow picks the scale by dividing the sizes, so ask for the sizes rounded down)
    small.draft('YCbCr', (full.size[0] // h, full.size[1] // v))
    w, ht = full.size
    out = Image.new('RGB', full.size)
    pixels = []
    for y in range(ht):
        for x in range(w):
            yy = luma.getpixel((x, y))
            _, cb, cr = small.getpixel((x // h, y // v))
            cb, cr = cb - 128, cr - 128
            r = yy + ((cr * 91881 + 32768) >> 16)
            g = yy + ((-22554 * cb - 46802 * cr + 32768) >> 16)
            b = yy + ((cb * 116130 + 32768) >> 16)
            pixels.append(tuple(max(0, min(255, c)) for c in (r, g, b)))
    out.putdata(pixels)
    return out


def over_background(rgba):
    """A pixel as it should look drawn over BACKGROUND (gfx.c's mix: rounded to the nearest)."""
    r, g, b, a = rgba
    return tuple((c * a + bg * (255 - a) + 127) // 255 for c, bg in zip((r, g, b), BACKGROUND))


def pictures():
    """(file name, bytes, expected RGBA pixels as a Pillow picture, how exact, size) for each picture."""
    out = []

    def add(name, data, tolerance, expected=None):
        img = expected if expected is not None else Image.open(io.BytesIO(data))
        img.seek(0)
        out.append((name, data, img.convert('RGBA'), tolerance))

    photo = scene(96, 72, 1)
    odd = scene(101, 67, 2)

    # PNG, saved by Pillow: different compression levels give stored, fixed and dynamic blocks
    add('p_rgb.png', saved(photo, 'PNG', compress_level=9), 'exact')
    add('p_store.png', saved(photo, 'PNG', compress_level=0), 'exact')
    add('p_fast.png', saved(odd, 'PNG', compress_level=1), 'exact')
    rgba = photo.copy()
    rgba.putalpha(Image.linear_gradient('L').resize(photo.size).rotate(90))
    add('p_rgba.png', saved(rgba, 'PNG'), 'exact')
    add('p_pal4.png', saved(photo.quantize(16), 'PNG', bits=4), 'exact')
    pal = photo.quantize(200)
    add('p_palt.png', saved(pal, 'PNG', transparency=5), 'exact')
    add('p_grey1.png', saved(photo.convert('1'), 'PNG'), 'exact')
    add('p_la.png', saved(rgba.convert('LA'), 'PNG'), 'exact')

    # PNG, by hand: every filter, 16 bit RGB, 2 bit grey, Adam7, and a see-through RGB colour
    w, h = 90, 70
    add('m_rgb16.png', png_by_hand(w, h, 2, 16, lambda x, y: ((x * 700) % 65536, (y * 900) % 65536, ((x + y) * 300) % 65536)),
        'exact', Image.frombytes('RGB', (w, h), bytes(v for y in range(h) for x in range(w)
                                                          for v in (((x * 700) % 65536) >> 8, ((y * 900) % 65536) >> 8, (((x + y) * 300) % 65536) >> 8))))
    add('m_grey2.png', png_by_hand(w, h, 0, 2, lambda x, y: ((x // 10 + y // 10) % 4,)),
        'exact', Image.frombytes('L', (w, h), bytes(((x // 10 + y // 10) % 4) * 85 for y in range(h) for x in range(w))))
    add('m_grey16.png', png_by_hand(w, h, 0, 16, lambda x, y: (((x * 3 + y * 2) % 256) * 257,)),
        'exact', Image.frombytes('L', (w, h), bytes((x * 3 + y * 2) % 256 for y in range(h) for x in range(w))))
    adam = lambda x, y: ((x * 3) % 256, (y * 5) % 256, (x ^ y) % 256, (x * y) % 256)
    add('m_adam7.png', png_by_hand(w, h, 6, 8, adam, interlace=True),
        'exact', Image.frombytes('RGBA', (w, h), bytes(v for y in range(h) for x in range(w) for v in adam(x, y))))
    plte = bytes([0, 0, 0, 255, 255, 0])
    add('m_pal1.png', png_by_hand(w, h, 3, 1, lambda x, y: ((x // 9 + y // 7) % 2,), interlace=True, plte=plte),
        'exact', Image.frombytes('RGB', (w, h), bytes(v for y in range(h) for x in range(w)
                                                          for v in ((255, 255, 0) if (x // 9 + y // 7) % 2 else (0, 0, 0)))))
    key = lambda x, y: (200, 30, 30) if (x // 15) % 2 == 0 else (30, 200, (y * 3) % 256)
    add('m_key.png', png_by_hand(w, h, 2, 8, key, trns=struct.pack('>HHH', 200, 30, 30)),
        'exact', Image.frombytes('RGBA', (w, h), bytes(v for y in range(h) for x in range(w)
                                                           for v in key(x, y) + ((0,) if key(x, y) == (200, 30, 30) else (255,)))))

    # GIF: a full palette (long LZW codes and table resets), few colours, interlaced, see-through,
    # and an animation (only its first picture is shown)
    add('g_full.gif', saved(photo.quantize(256), 'GIF', interlace=False), 'exact')
    add('g_four.gif', saved(photo.quantize(4), 'GIF', interlace=False), 'exact')
    add('g_inter.gif', saved(odd.quantize(64), 'GIF', interlace=True), 'exact')
    add('g_trans.gif', saved(photo.quantize(32), 'GIF', transparency=3), 'exact')
    frames = [photo.quantize(64), scene(96, 72, 9).quantize(64)]
    add('g_anim.gif', saved(frames[0], 'GIF', save_all=True, append_images=frames[1:], duration=100), 'exact')

    # JPEG: baseline with each kind of colour subsampling, grey, progressive, an odd size,
    # restart markers and the best quality. Those with smaller colour planes are compared with
    # jpeg_unsmoothed, as Pillow smooths the colour as it stretches it and AerOS doesn't
    def jpeg(name, img, h=1, v=1, **options):
        data = saved(img, 'JPEG', **options)
        add(name, data, 'jpeg', jpeg_unsmoothed(data, h, v) if (h, v) != (1, 1) else None)

    jpeg('j_444.jpg', photo, quality=85, subsampling=0)
    # 4:2:2 (colour half as wide) can't be asked for unstretched (Pillow scales both ways at
    # once), so it is compared with Pillow's smoothed colour, more loosely. It goes through the
    # same code as 4:2:0, which is checked exactly
    add('j_422.jpg', saved(photo, 'JPEG', quality=85, subsampling=1), 'jpeg smoothed')
    jpeg('j_420.jpg', photo, 2, 2, quality=85, subsampling=2)
    jpeg('j_grey.jpg', photo.convert('L'), quality=90)
    jpeg('j_prog.jpg', photo, 2, 2, quality=85, progressive=True)
    jpeg('j_prog4.jpg', photo, quality=80, progressive=True, subsampling=0)
    jpeg('j_progg.jpg', odd.convert('L'), quality=75, progressive=True)
    jpeg('j_odd.jpg', odd, 2, 2, quality=85)
    jpeg('j_q100.jpg', photo, quality=100, subsampling=0)
    try:
        jpeg('j_rst.jpg', odd, 2, 2, quality=85, restart_marker_blocks=3)
    except TypeError:
        pass  # an older Pillow, without restart markers
    return out


def damaged(good):
    """Damaged versions of some of the pictures: cut short, bytes changed at random, and headers
    that claim a huge size. Each must be turned down or drawn partly, never crash AerOS."""
    rnd = random.Random(7)
    out = []
    for name, data, _, _ in good:
        if name not in ('p_rgb.png', 'm_adam7.png', 'g_full.gif', 'g_inter.gif', 'j_420.jpg', 'j_prog.jpg', 'j_rst.jpg'):
            continue
        out.append(data[:len(data) // 2])
        out.append(data[:len(data) - 10])
        for _ in range(3):
            broken = bytearray(data)
            for _ in range(20):
                broken[rnd.randrange(16, len(broken))] = rnd.randrange(256)
            out.append(bytes(broken))
    # sizes far too big, which must be turned down before any memory is used
    png = bytearray(good[0][1])
    png[16:24] = struct.pack('>II', 16384, 16384)
    out.append(bytes(png))
    gif = bytearray([d for n, d, _, _ in good if n == 'g_full.gif'][0])
    gif[6:10] = struct.pack('<HH', 65535, 65535)
    out.append(bytes(gif))
    jpg = bytearray([d for n, d, _, _ in good if n == 'j_444.jpg'][0])
    at = jpg.find(b'\xff\xc0')
    jpg[at + 5:at + 9] = struct.pack('>HH', 65535, 65535)
    out.append(bytes(jpg))
    # just the start of each kind, and nothing else
    out += [b'\x89PNG\r\n\x1a\n', b'GIF89a', b'\xff\xd8\xff', b'GIF89a' + bytes(7) + b'\x2c' + bytes(9) + b'\x08\x01\xff\x00']
    return out


def place(n):
    """Where the n-th picture's cell is: rows of COLUMNS cells from the top left."""
    return (n % COLUMNS) * CELL_W, (n // COLUMNS) * CELL_H


def test_pictures(t):
    if Image is None:
        raise Skip("needs Python's Pillow package: pip install pillow")
    good = pictures()
    bad = damaged(good)
    big = saved(scene(1920, 1080, 3), 'JPEG', quality=80, progressive=True)

    files = {}
    for name, data, _, _ in good:
        path = t.path(name)
        with open(path, 'wb') as f:
            f.write(data)
        files['/apps/pictest/' + name] = path
    bad_names = []
    for i, data in enumerate(bad):
        name = f'bad{i:02d}.' + ('png' if data[:1] == b'\x89' else 'gif' if data[:1] == b'G' else 'jpg')
        bad_names.append(name)
        with open(t.path(name), 'wb') as f:
            f.write(data)
        files['/apps/pictest/' + name] = t.path(name)
    with open(t.path('big.jpg'), 'wb') as f:
        f.write(big)
    files['/apps/pictest/big.jpg'] = t.path('big.jpg')

    # the program: every picture's size (before taking the screen over, so it can be printed),
    # then every picture drawn in its cell, the damaged ones in a corner, the big photo shrunk,
    # and the marker that says it's done
    names = [name for name, _, _, _ in good]
    lines = ['let GOOD = [' + ', '.join(f'"{n}"' for n in names) + ']',
             'let BAD = [' + ', '.join(f'"{n}"' for n in bad_names) + ']',
             'for name in GOOD do',
             '    let size = imgsz(rdb("/apps/pictest/" + name))',
             '    print("size " + name + " " + str(size))',
             'end',
             'gopen()',
             f'gclear(rgb({BACKGROUND[0]}, {BACKGROUND[1]}, {BACKGROUND[2]}))',
             'let failed = []',
             'let i = 0',
             'for name in GOOD do',
             f'    if not image(rdb("/apps/pictest/" + name), (i % {COLUMNS}) * {CELL_W}, (i / {COLUMNS}) * {CELL_H}) then push(failed, name) end',
             '    i = i + 1',
             'end',
             'let survived = 0',
             'for name in BAD do',
             '    let d = rdb("/apps/pictest/" + name)',
             '    imgsz(d)',
             '    image(d, 900, 600, 100, 100)',
             '    survived = survived + 1',
             'end',
             'let started = time()',
             'let big_ok = image(rdb("/apps/pictest/big.jpg"), 0, 600, 256, 144)',
             'let took = time() - started',
             'rect(1016, 760, 8, 8, rgb(255, 0, 255))',
             'key()',
             'gclose()',
             'print("failed " + str(failed))',
             'print("survived " + survived + " of " + len(BAD))',
             'print("big " + big_ok + " in " + took + " ms")']
    with open(t.path('pictest.aer'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    files['/apps/pictest.aer'] = t.path('pictest.aer')

    aeros = t.boot(graphics=True, disk=t.disk(size_mb=64, files=files), memory=512)
    aeros.first_login()
    aeros.ask('pictest')

    # wait for the marker, then look at every picture's cell
    start = time.time()
    while True:
        width, height, pixels = aeros.screenshot('pictures')

        def at(x, y):
            i = (y * width + x) * 3
            return tuple(pixels[i:i + 3])
        if at(width - 4, height - 4) == MARKER:
            break
        if time.time() - start > 180:
            aeros.fail('the pictures were not all drawn within 3 minutes (serial log: %r)' % aeros.serial()[-2000:])
        time.sleep(1)

    problems = []
    report = []
    for n, (name, data, expected, tolerance) in enumerate(good):
        x0, y0 = place(n)
        w, h = expected.size
        worst, total = 0, 0
        for y in range(h):
            for x in range(w):
                want = over_background(expected.getpixel((x, y)))
                got = at(x0 + x, y0 + y)
                d = max(abs(a - b) for a, b in zip(want, got))
                worst = max(worst, d)
                total += d
        mean = total / (w * h)
        report.append(f'{name}: worst {worst}, mean {mean:.2f}')
        # exact pictures must match exactly; a JPEG may differ a little, by rounding
        if tolerance == 'exact':
            ok = worst == 0
        elif tolerance == 'jpeg':
            ok = worst <= 6 and mean <= 1.0
        else:
            ok = worst <= 90 and mean <= 9.0
        if not ok:
            problems.append(f'{name} is drawn wrong: worst difference {worst}, mean {mean:.2f}')

    aeros.type('x')
    aeros.wait_for('big ', 60)
    printed = aeros.serial()
    with open(t.path('report.txt'), 'w') as f:
        f.write('\n'.join(report) + '\n\n' + printed[-3000:])
    for name, _, expected, _ in good:
        if f'size {name} [{expected.size[0]}, {expected.size[1]}]' not in printed:
            problems.append(f'imgsz gave the wrong size for {name}')
    if 'failed []' not in printed:
        problems.append('image() turned some good pictures down: ' + printed[printed.find('failed'):][:300])
    if f'survived {len(bad)} of {len(bad)}' not in printed:
        problems.append('AerOS did not get through all the damaged pictures')
    if 'big true' not in printed:
        problems.append('the 1920 x 1080 progressive JPEG was not drawn')
    if problems:
        aeros.fail('\n'.join(problems) + '\n\n' + '\n'.join(report))
    aeros.quit()
