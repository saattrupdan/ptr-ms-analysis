#!/usr/bin/env python3
"""Draw the Sniff mark and write it out as an icon set.

There is one copy of the artwork: the primitives below. ``--svg`` renders them
to SVG for the web page and for ``gfx/sniff.svg``, and the rasteriser draws the
same primitives into a bitmap, so the Dock icon and the header mark cannot drift
apart the way a hand-redrawn PNG always eventually does.

No image library is involved. The raster is a plain RGBA grid written as PNG by
hand, which means the same script runs on a macOS runner (for ``.icns`` via
``iconutil``) and on a Windows runner (for ``.ico``) without installing
anything, and a checkout stays buildable with only Python.

Usage:
    python packaging/make_icons.py --svg gfx/sniff.svg
    python packaging/make_icons.py --icns build/icons/sniff.icns
    python packaging/make_icons.py --ico build/icons/sniff.ico
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess  # nosec B404 - iconutil is the documented way to build an .icns
import sys
import tempfile
import zlib

# ---------------------------------------------------------------------------
# The artwork, in a 64x64 field. A rounded teal tile, a quiet spectrum baseline
# with one narrow peak, and a deliberately small lab-coated mascot studying that
# peak through a magnifying glass. These primitives are the canonical geometry for
# both the bitmap and the SVG; keeping them here prevents the two marks drifting.
# ---------------------------------------------------------------------------

FIELD = 64.0
TILE = (0x1F, 0x6F, 0x6B, 255)
TRACE_COLOUR = (0xEA, 0xFA, 0xF6, 255)
MASCOT_SKIN_COLOUR = (0xFF, 0xD9, 0xA8, 255)
LAB_COAT_COLOUR = (0xF7, 0xFB, 0xFF, 255)
COAT_ACCENT = (0x9B, 0xD9, 0xD5, 255)
GLASS_COLOUR = (0xB8, 0xE8, 0xE4, 160)
RADIUS = 14.0                      # of the tile, in field units
TRACE = [(6, 46), (20, 46), (24, 46), (26, 27), (28, 46), (58, 46)]
TRACE_WIDTH = 3.6
# The mascot is on the right and its eye is turned left, towards the spectrum peak.
MASCOT_BODY = [(42, 29), (51, 29), (54, 34), (56, 46), (38, 46), (40, 34)]
MASCOT_HEAD = (47.0, 22.0, 5.8)    # centre x, centre y, radius
MASCOT_EYE = (43.6, 21.0, 1.45)    # the eye sits on the peak-facing side
MASCOT_PUPIL = (43.0, 21.0, 0.62)
COLLAR = [(44.8, 29), (48, 34), (51.0, 29)]
COAT_SEAM = [(48, 34), (48, 45)]
COAT_BUTTONS = ((49.7, 36.5, 0.72), (49.7, 40.0, 0.72))
MAGNIFIER_LENS = (30.0, 31.0, 5.8)  # the lens sits over the spectrum peak
MAGNIFIER_INNER = 4.15
MAGNIFIER_HANDLE = [(34.0, 35.0), (42.0, 43.0)]
MAGNIFIER_WIDTH = 2.2
SMALL_TRACE = [(6, 46), (21, 46), (26, 27), (31, 46), (58, 46)]
SMALL_WIDTH = 6.4
# At 32 px the mascot remains readable in a header/icon; at 16 px the trace alone
# avoids turning the mark into an indistinct cluster of pixels.
DETAIL_MIN = 24

# iconutil accepts exactly these names. A 64x64 entry, however reasonable it looks,
# makes it reject the whole iconset with "Invalid Iconset".
ICNS_SIZES = (16, 32, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


# ---------------------------------------------------------------------------
# Rasterising
# ---------------------------------------------------------------------------

def _coverage(px, py, cx, cy, r):
    """Fraction of a pixel's area inside a circle, by 3x3 subsampling."""
    hit = 0
    for dy in (-0.33, 0.0, 0.33):
        for dx in (-0.33, 0.0, 0.33):
            if (px + dx - cx) ** 2 + (py + dy - cy) ** 2 <= r * r:
                hit += 1
    return hit / 9.0


def _stamp(buf, size, cx, cy, r, colour):
    """Paint one round dot, antialiased, over the buffer. Coordinates and radius
    are in device pixels: every caller scales from the 64-unit field once, because
    scaling twice is how the trace ended up four times too thick."""
    lo_x, hi_x = max(int(cx - r) - 1, 0), min(int(cx + r) + 2, size)
    lo_y, hi_y = max(int(cy - r) - 1, 0), min(int(cy + r) + 2, size)
    for y in range(lo_y, hi_y):
        for x in range(lo_x, hi_x):
            a = _coverage(x + 0.5, y + 0.5, cx, cy, r)
            if a <= 0.0:
                continue
            _blend(buf, size, x, y, colour, a)


def _blend(buf, size, x, y, colour, alpha):
    i = (y * size + x) * 4
    if i + 3 >= len(buf):
        return
    d = buf[i : i + 4]
    out = _over(d, colour, alpha)
    buf[i : i + 4] = out


def _over(dst, src, alpha):
    """Source-over, with the source colour already scaled by coverage."""
    sa = src[3] / 255.0 * alpha
    da = dst[3] / 255.0
    oa = sa + da * (1.0 - sa)
    if oa <= 0:
        return bytes((0, 0, 0, 0))
    c = [
        (src[i] * sa + dst[i] * da * (1.0 - sa)) / oa for i in range(3)
    ]
    return bytes((int(round(c[0])), int(round(c[1])), int(round(c[2])), int(round(oa * 255))))


def _stroke(buf, size, points, width, colour):
    """A polyline with round caps and round joins, drawn as a run of dots."""
    scale = size / FIELD
    w = max(width * scale, 0.9)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = max(((dx * dx + dy * dy) ** 0.5) * scale, 1e-6)
        steps = max(int(length / max(w * 0.34, 0.6)), 1)
        for i in range(steps + 1):
            t = i / steps
            _stamp(buf, size, (x0 + dx * t) * scale, (y0 + dy * t) * scale, w / 2.0, colour)
    for x, y in (points[0], points[-1]):
        _stamp(buf, size, x * scale, y * scale, w / 2.0, colour)
    for x, y in (points[0], points[-1]):
        _stamp(buf, size, x * scale, y * scale, w / 2.0, colour)


def _polygon(buf, size, points, colour):
    """Fill a closed silhouette in device pixels."""
    scale = size / FIELD
    scaled = [(x * scale, y * scale) for x, y in points]
    lo_x = max(int(min(x for x, _ in scaled)) - 1, 0)
    hi_x = min(int(max(x for x, _ in scaled)) + 2, size)
    lo_y = max(int(min(y for _, y in scaled)) - 1, 0)
    hi_y = min(int(max(y for _, y in scaled)) + 2, size)
    for y in range(lo_y, hi_y):
        for x in range(lo_x, hi_x):
            inside = False
            for (x0, y0), (x1, y1) in zip(scaled, scaled[1:] + scaled[:1]):
                if (y0 > y + 0.5) != (y1 > y + 0.5):
                    cross = (x1 - x0) * (y + 0.5 - y0) / (y1 - y0) + x0
                    if x + 0.5 < cross:
                        inside = not inside
            if inside:
                _blend(buf, size, x, y, colour, 1.0)


def render(size, detail=None):
    """An RGBA byte string for one square icon of ``size`` pixels."""
    if detail is None:
        detail = size >= DETAIL_MIN
    buf = bytearray(size * size * 4)
    tile_colour = TILE

    # Tile: a rounded square. Filled row by row, corners checked against the
    # quarter-circle, which is exact for a rounded rect.
    r = RADIUS / FIELD * size
    for y in range(size):
        for x in range(size):
            cx = min(max(x + 0.5, r), size - r)
            cy = min(max(y + 0.5, r), size - r)
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                i = (y * size + x) * 4
                buf[i : i + 4] = bytes(tile_colour)

    def paint(points, width, colour):
        _stroke(buf, size, points, width, colour)

    if detail:
        paint(TRACE, TRACE_WIDTH, TRACE_COLOUR)
        _polygon(buf, size, MASCOT_BODY, LAB_COAT_COLOUR)
        _polygon(buf, size, COLLAR, COAT_ACCENT)
        paint(COAT_SEAM, 1.0, COAT_ACCENT)
        s = size / FIELD
        for x, y, radius in COAT_BUTTONS:
            _stamp(buf, size, x * s, y * s, radius * s, TILE)
        _stamp(buf, size, MASCOT_HEAD[0] * s, MASCOT_HEAD[1] * s,
               MASCOT_HEAD[2] * s, MASCOT_SKIN_COLOUR)
        _stamp(buf, size, MASCOT_EYE[0] * s, MASCOT_EYE[1] * s,
               MASCOT_EYE[2] * s, LAB_COAT_COLOUR)
        _stamp(buf, size, MASCOT_PUPIL[0] * s, MASCOT_PUPIL[1] * s,
               MASCOT_PUPIL[2] * s, TILE)
        paint(MAGNIFIER_HANDLE, MAGNIFIER_WIDTH, MASCOT_SKIN_COLOUR)
        _stamp(buf, size, MAGNIFIER_LENS[0] * s, MAGNIFIER_LENS[1] * s,
               MAGNIFIER_LENS[2] * s, MASCOT_SKIN_COLOUR)
        _stamp(buf, size, MAGNIFIER_LENS[0] * s, MAGNIFIER_LENS[1] * s,
               MAGNIFIER_INNER * s, GLASS_COLOUR)
    else:
        paint(SMALL_TRACE, SMALL_WIDTH, TRACE_COLOUR)
    return bytes(buf)


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

def png_bytes(size, rgba):
    """A 8-bit RGBA PNG, filter 0 on every row, written without an image library."""
    raw = b"".join(b"\x00" + rgba[y * size * 4 : (y + 1) * size * 4] for y in range(size))

    def chunk(kind, payload):
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(sizes):
    """An .ico whose entries are PNGs, which Vista and later read natively."""
    entries = [(s, png_bytes(s, render(s))) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        w = 0 if size >= 256 else size
        out += struct.pack(
            "<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    for _, data in entries:
        out += data
    return out


def icns_file(path, sizes=ICNS_SIZES):
    """Build an .icns with iconutil, from an .iconset made here."""
    # The suffix is not decoration: iconutil only recognises a folder named *.iconset.
    iconset = tempfile.mkdtemp(prefix="sniff-", suffix=".iconset")
    try:
        for s in sizes:
            for scale, label in ((1, ""), (2, "@2x")):
                px = s * scale
                with open(
                    os.path.join(iconset, "icon_%dx%d%s.png" % (s, s, label)), "wb"
                ) as handle:
                    handle.write(png_bytes(px, render(px)))
        target = os.path.abspath(path)
        subprocess.run(  # nosec B603 - fixed arguments, iconutil from the OS
            ["iconutil", "-c", "icns", "-o", target, iconset], check=True
        )
    finally:
        for name in os.listdir(iconset):
            os.unlink(os.path.join(iconset, name))
        os.rmdir(iconset)


# ---------------------------------------------------------------------------
# SVG, from the same primitives
# ---------------------------------------------------------------------------

def svg_text(size=FIELD):
    def pts(points):
        return " ".join("%g,%g" % (x, y) for x, y in points)

    body = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        'width="%g" height="%g" role="img" aria-label="Sniff">' % (size, size),
        '  <rect width="64" height="64" rx="%g" fill="#1f6f6b"/>' % RADIUS,
        '  <polyline points="%s" fill="none" stroke="#eafaf6" stroke-width="%g"'
        ' stroke-linejoin="round" stroke-linecap="round"/>' % (pts(TRACE), TRACE_WIDTH),
        '  <polygon points="%s" fill="#f7fbff"/>' % pts(MASCOT_BODY),
        '  <polygon points="%s" fill="#9bd9d5"/>' % pts(COLLAR),
        '  <polyline points="%s" fill="none" stroke="#9bd9d5" stroke-width="1"'
        ' stroke-linejoin="round" stroke-linecap="round"/>' % pts(COAT_SEAM),
        '  <circle cx="%g" cy="%g" r="%g" fill="#1f6f6b"/>' % COAT_BUTTONS[0],
        '  <circle cx="%g" cy="%g" r="%g" fill="#1f6f6b"/>' % COAT_BUTTONS[1],
        '  <circle cx="%g" cy="%g" r="%g" fill="#ffd9a8"/>' % MASCOT_HEAD,
        '  <circle cx="%g" cy="%g" r="%g" fill="#f7fbff"/>' % MASCOT_EYE,
        '  <circle cx="%g" cy="%g" r="%g" fill="#1f6f6b"/>' % MASCOT_PUPIL,
        '  <polyline points="%s" fill="none" stroke="#ffd9a8" stroke-width="%g"'
        ' stroke-linejoin="round" stroke-linecap="round"/>' % (
            pts(MAGNIFIER_HANDLE), MAGNIFIER_WIDTH),
        '  <circle cx="%g" cy="%g" r="%g" fill="#ffd9a8"/>' % MAGNIFIER_LENS,
        '  <circle cx="%g" cy="%g" r="%g" fill="#b8e8e4" fill-opacity="%g"/>' % (
            MAGNIFIER_LENS[0], MAGNIFIER_LENS[1], MAGNIFIER_INNER,
            GLASS_COLOUR[3] / 255),
        "</svg>",
    ]
    return "\n".join(body) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--svg", metavar="PATH", help="write the mark as SVG")
    ap.add_argument("--png", metavar="PATH", help="write one PNG (see --size)")
    ap.add_argument("--size", type=int, default=256, help="size for --png")
    ap.add_argument("--ico", metavar="PATH", help="write a Windows icon file")
    ap.add_argument("--icns", metavar="PATH", help="write a macOS icon file")
    args = ap.parse_args(argv)

    if not (args.svg or args.png or args.ico or args.icns):
        ap.error("nothing to do: pass --svg, --png, --ico or --icns")
    for path in (args.svg, args.png, args.ico, args.icns):
        if path:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as handle:
            handle.write(svg_text())
    if args.png:
        with open(args.png, "wb") as handle:
            handle.write(png_bytes(args.size, render(args.size)))
    if args.ico:
        with open(args.ico, "wb") as handle:
            handle.write(ico_bytes(ICO_SIZES))
    if args.icns:
        icns_file(args.icns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
