"""The logo: the bars of a level meter on a dark tile.

The logo is drawn here, so the app ships no third-party artwork.
``scripts/make_logo.py`` writes it to ``docs/logo.svg`` and, in the sizes a
window icon needs, to ``assets``; those bundled files are what the app shows.
"""

import math
import struct
import zlib
from pathlib import Path

# Pixels a side. The largest comes first: macOS uses only the first icon.
ICON_SIZES = (256, 64, 32, 16)

TILE = (0x18, 0x18, 0x1B)
BARS = (0xE5, 0x39, 0x35)

# Everything below is in shares of the tile's side.
_CORNER = 0.225  # radius of the tile's corners
_BAR_WIDTH = 0.092
_BAR_GAP = 0.058
_BAR_HEIGHTS = (0.22, 0.44, 0.64, 0.36, 0.20)

type _Box = tuple[float, float, float, float]  # left, top, right, bottom


def _bars(side: float = 1.0) -> list[_Box]:
    """Return the bars, centred on a tile with the given side."""
    count = len(_BAR_HEIGHTS)
    step = _BAR_WIDTH + _BAR_GAP
    left = (1 - count * step + _BAR_GAP) / 2
    return [
        (
            (left + index * step) * side,
            (1 - height) / 2 * side,
            (left + index * step + _BAR_WIDTH) * side,
            (1 + height) / 2 * side,
        )
        for index, height in enumerate(_BAR_HEIGHTS)
    ]


def render_svg() -> str:
    """Return the logo as an SVG image."""

    def rectangle(box: _Box, radius: float, colour: tuple[int, int, int]) -> str:
        left, top, right, bottom = (round(value * 512, 2) for value in box)
        return (
            f'  <rect x="{left}" y="{top}" width="{round(right - left, 2)}" '
            f'height="{round(bottom - top, 2)}" rx="{round(radius * 512, 2)}" '
            f'fill="#{bytes(colour).hex()}"/>'
        )

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img">',
        "  <title>Kleomarcus Announcer</title>",
        rectangle((0, 0, 1, 1), _CORNER, TILE),
        *(rectangle(bar, _BAR_WIDTH / 2, BARS) for bar in _bars()),
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def render_png(size: int) -> bytes:
    """Return the logo as a square PNG image with ``size`` pixels a side."""
    shapes = _bars(size)
    pixels = bytearray()
    for row in range(size):
        for column in range(size):
            x, y = column + 0.5, row + 0.5
            tile = _coverage(x, y, (0, 0, size, size), _CORNER * size)
            bar = max(_coverage(x, y, shape, _BAR_WIDTH * size / 2) for shape in shapes)
            pixels += bytes(
                round(under + (over - under) * bar)
                for under, over in zip(TILE, BARS, strict=True)
            )
            pixels.append(round(255 * tile))

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + checksum

    # Every row of pixels begins with the number of its filter: none.
    rows = b"".join(
        b"\x00" + pixels[row * size * 4 : (row + 1) * size * 4] for row in range(size)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def _coverage(x: float, y: float, box: _Box, radius: float) -> float:
    """Return how much of the pixel around a point lies in a rounded rectangle."""
    left, top, right, bottom = box
    across = abs(x - (left + right) / 2) - ((right - left) / 2 - radius)
    down = abs(y - (top + bottom) / 2) - ((bottom - top) / 2 - radius)
    distance = (
        math.hypot(max(across, 0), max(down, 0)) + min(max(across, down), 0) - radius
    )
    return min(max(0.5 - distance, 0), 1)


def icon_path(size: int) -> Path:
    """Return where the bundled icon with ``size`` pixels a side lives."""
    return Path(__file__).parent / "assets" / f"icon-{size}.png"
