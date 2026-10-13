#!/usr/bin/env python3
"""Draw the gear for the Settings button.

Two tones are written because the button sits on either a light or a dark
theme: ``dark`` is the glyph for light backgrounds, ``light`` for dark ones.
Run it by hand after changing the artwork - the PNGs are committed, so normal
builds never need Pillow:

    python -m pip install pillow
    python assets/make_settings_icon.py

Each image is drawn at 8x and downsampled, which is what keeps the teeth and
the centre hole clean at 20px.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SUPERSAMPLE = 8
TONES = {"dark": "#5a6270", "light": "#c8d0da"}     # dark glyph on light, and back
SIZES = (20, 32)                                    # 20 native, 32 for scaled screens
TEETH = 8


def gear(size, colour):
    """A gear silhouette: tooth ring, body, and a transparent centre hole."""
    scale = size * SUPERSAMPLE
    canvas = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    centre = scale / 2
    outer, body, hole = scale * 0.47, scale * 0.36, scale * 0.16

    outline = []
    steps = 720
    for step in range(steps):
        angle = 2 * math.pi * step / steps
        phase = (step / steps) * TEETH % 1
        radius = outer if 0.25 <= phase <= 0.75 else body
        outline.append((centre + radius * math.cos(angle), centre + radius * math.sin(angle)))
    draw.polygon(outline, fill=colour)
    draw.ellipse([centre - hole, centre - hole, centre + hole, centre + hole], fill=(0, 0, 0, 0))

    return canvas.resize((size, size), Image.LANCZOS)


def main():
    written = []
    for tone, colour in TONES.items():
        for size in SIZES:
            target = HERE / f"settings_gear_{tone}_{size}.png"
            gear(size, colour).save(target)
            written.append(target.name)
    print("wrote " + ", ".join(written))


if __name__ == "__main__":
    main()
