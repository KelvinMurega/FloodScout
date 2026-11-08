"""Draw FloodScout's app icons (the header's water drop) as PNGs, with no
image library: the drop and its wave are sampled 4 x 4 per pixel and written
with zlib. Run once; the PNGs are committed.

    python scripts/make_icons.py
"""

import bisect
import math
import struct
import zlib
from pathlib import Path

BRAND = (0x1F, 0x78, 0xD1)
WHITE = (255, 255, 255)
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icons"


def cubic(p0, p1, p2, p3, n=40):
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        yield (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def drop():
    """The drop's outline in the logo's 32 x 32 box."""
    pts = list(cubic((16, 3), (20.8, 9), (25, 14), (25, 19)))
    for i in range(1, 60):  # the lower half circle, centre (16, 19), radius 9
        angle = math.pi * i / 60
        pts.append((16 + 9 * math.cos(angle), 19 + 9 * math.sin(angle)))
    pts += list(cubic((7, 19), (7, 14), (11.2, 9), (16, 3)))
    return pts


def wave():
    first = list(cubic((10.5, 20.5), (12.3, 21.9), (14.1, 21.9), (16, 20.5)))
    second = list(cubic((16, 20.5), (17.9, 19.1), (19.6, 19.1), (21.5, 20.5)))
    return first + second[1:]


def crossings(y, poly):
    """Where a horizontal line at `y` crosses the outline, left to right."""
    xs = []
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y):
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return sorted(xs)


def inside(x, xs):
    return bisect.bisect_left(xs, x) % 2 == 1


def near(x, y, line, width):
    for (x1, y1), (x2, y2) in zip(line, line[1:]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1)))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) <= width / 2:
            return True
    return False


def draw(size, scale):
    """`scale`: how much of the icon the 32-unit logo fills (maskable icons need room)."""
    outline, line = drop(), wave()
    unit = size * scale / 32
    offset = (size - 32 * unit) / 2
    rows = []
    for py in range(size):
        ys = [(py + (sy + 0.5) / 4 - offset) / unit for sy in range(4)]
        cuts = [crossings(y, outline) for y in ys]
        row = bytearray([0])
        for px in range(size):
            white = wavey = 0
            for sx in range(4):
                x = (px + (sx + 0.5) / 4 - offset) / unit
                for y, xs in zip(ys, cuts):
                    if inside(x, xs):
                        # The wave lies in x 9.5..22.5, y 18.5..22.5.
                        if 9.4 < x < 22.6 and 18.4 < y < 22.6 and near(x, y, line, 2):
                            wavey += 1
                        else:
                            white += 1
            share_white, share_wave = white / 16, wavey / 16
            share_bg = 1 - share_white - share_wave
            row += bytes(
                round(share_bg * b + share_white * w + share_wave * b)
                for b, w in zip(BRAND, WHITE)
            )
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, scale in (
        ("icon-192.png", 192, 0.75),
        ("icon-512.png", 512, 0.75),
        ("maskable-512.png", 512, 0.55),
        ("apple-touch-icon.png", 180, 0.7),
        ("favicon-32.png", 32, 0.9),
    ):
        (OUT / name).write_bytes(draw(size, scale))
        print(name)
