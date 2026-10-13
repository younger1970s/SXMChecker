#!/usr/bin/env python3
"""Generate the SXMChecker application icon.

Original artwork: a satellite with signal arcs on a navy tile - no third-party
logos or trademarks are used. Run once to (re)create the committed assets:

    python assets/make_icon.py

Needs Pillow (build-time only); normal builds just use the generated .ico.
Each size is rendered natively so small variants stay sharp, and the .ico is
assembled by hand with PNG-compressed entries.
"""
import struct
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG_TOP = (9, 24, 54)
BG_BOTTOM = (26, 74, 148)
PANEL = (52, 104, 186)
PANEL_LINE = (150, 192, 242)
CONNECTOR = (196, 208, 226)
METAL = (236, 241, 248)
METAL_EDGE = (168, 184, 206)
SCREEN = (22, 48, 92)
AMBER = (255, 176, 60)


def _gradient(size):
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        draw.line([(0, y), (size, y)],
                  fill=tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return img


def render(px):
    """Draw the icon at exactly ``px`` pixels."""
    scale = px / 256.0
    s = lambda value: int(round(value * scale))
    edge = lambda value: max(1, s(value))

    tile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=s(56), fill=255)
    tile.paste(_gradient(px), (0, 0), mask)
    draw = ImageDraw.Draw(tile)

    # Solar panels either side of the body.
    for x0, x1 in ((18, 82), (174, 238)):
        draw.rounded_rectangle([s(x0), s(138), s(x1), s(178)], radius=s(6),
                               fill=PANEL, outline=PANEL_LINE, width=edge(2))
        for fraction in (0.25, 0.5, 0.75):
            x = x0 + (x1 - x0) * fraction
            draw.line([s(x), s(142), s(x), s(174)], fill=PANEL_LINE, width=edge(1))
        draw.line([s(x0 + 3), s(158), s(x1 - 3), s(158)], fill=PANEL_LINE, width=edge(1))

    # Connectors between the panels and the central body.
    for x0, x1 in ((82, 98), (158, 174)):
        draw.rectangle([s(x0), s(150), s(x1), s(166)], fill=CONNECTOR)

    # Satellite body with a small screen.
    draw.rounded_rectangle([s(96), s(130), s(160), s(186)], radius=s(12),
                           fill=METAL, outline=METAL_EDGE, width=edge(2))
    draw.rounded_rectangle([s(108), s(143), s(148), s(167)], radius=s(4), fill=SCREEN)

    # Signal arcs radiating upward.
    for radius in (44, 64, 84):
        draw.arc([s(128 - radius), s(120 - radius), s(128 + radius), s(120 + radius)],
                 start=200, end=340, fill=AMBER, width=edge(9))
    return tile


def write_ico(path, images):
    """Assemble a multi-size .ico with PNG-compressed entries."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, image in images:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        dimension = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
        blobs.append(data)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in entries:
            handle.write(entry)
        for data in blobs:
            handle.write(data)


def main():
    images = [(size, render(size)) for size in SIZES]
    images[-1][1].save(HERE / "sxm_icon.png")
    write_ico(HERE / "sxm_icon.ico", images)
    print(f"wrote {HERE / 'sxm_icon.ico'} ({', '.join(str(s) for s in SIZES)})")
    print(f"wrote {HERE / 'sxm_icon.png'}")


if __name__ == "__main__":
    main()
