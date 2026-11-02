"""Build size-optimized Windows icons from the approved ConnectionPilot logo.

Small sizes are redrawn deliberately: simply scaling the 256px art makes the
briefcase flap and arrow merge into a low-contrast blur at taskbar sizes.
The high-resolution PNG stays the canonical, approved artwork.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def small_icon(size: int) -> Image.Image:
    """Design for the actual final pixel size, with no micro-details."""
    ss = 12
    scale = size * ss / 100
    def p(*coords):
        return tuple(round(v * scale) for v in coords)
    image = Image.new('RGBA', (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    navy = '#071c49'
    white = '#ffffff'
    blue = '#168bff'
    # The transparent margin is intentional: no opaque square corners.
    d.rounded_rectangle(p(2, 2, 98, 98), radius=round(22 * scale), fill=navy)
    # One bold briefcase handle and solid body; a detailed clasp is unreadable
    # below 32 px, so it is intentionally omitted from these sizes.
    d.rounded_rectangle(p(34, 22, 66, 43), radius=round(8 * scale), fill=white)
    d.rounded_rectangle(p(41, 28, 59, 42), radius=round(4 * scale), fill=navy)
    d.rounded_rectangle(p(16, 39, 82, 78), radius=round(8 * scale), fill=white)
    if size >= 32:
        # Clear flap separation on medium sizes; skip at 16/20/24.
        d.line((p(17, 57), p(36, 62), p(54, 64)), fill=navy,
               width=max(1,round(3.5*scale)), joint='curve')
    # Thick dark edge keeps the navigation arrow crisp even where it crosses
    # the white briefcase, especially at 16 and 20 logical pixels.
    d.polygon([p(55, 68), p(94, 51), p(78, 94), p(72, 78)], fill=navy)
    d.polygon([p(60, 68), p(89, 55), p(77, 88), p(72, 75)], fill=blue)
    result = image.resize((size, size), Image.Resampling.LANCZOS)
    # Lanczos can give corner pixels alpha=1–2 from ringing. Windows will
    # otherwise render faint dark corners on contrasting taskbars.
    for xy in ((0,0), (0,size-1), (size-1,0), (size-1,size-1)):
        result.putpixel(xy, (0,0,0,0))
    return result


def icon_frames() -> dict[int, Image.Image]:
    canonical = Image.open(ASSETS / 'connectionpilot.png').convert('RGBA')
    return {n: small_icon(n) if n <= 48 else canonical.resize((n,n), Image.Resampling.LANCZOS)
            for n in SIZES}


def write_ico(frames: dict[int, Image.Image], output: Path) -> None:
    """Write PNG-encoded frames explicitly; Pillow's ICO saver resizes a base
    image, silently discarding hand-tuned small variants.
    """
    offsets = 6 + len(frames) * 16
    entries, blobs = [], []
    for size, im in sorted(frames.items()):
        buf = io.BytesIO()
        im.save(buf, format='PNG', optimize=True)
        data = buf.getvalue()
        entries.append(struct.pack('<BBBBHHII', 0 if size==256 else size,
                                   0 if size==256 else size, 0, 0, 1, 32,
                                   len(data), offsets))
        blobs.append(data)
        offsets += len(data)
    output.write_bytes(struct.pack('<HHH', 0,1,len(entries)) + b''.join(entries) + b''.join(blobs))


def build() -> None:
    frames = icon_frames()
    write_ico(frames, ASSETS / 'connectionpilot.ico')
    frames[48].save(ASSETS / 'connectionpilot-header.png', optimize=True)


if __name__ == '__main__':
    build()
