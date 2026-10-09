"""Makes the two background pictures in public/art/ from the owner's artwork (#background).

Usage: python3 scripts/make-art.py path/to/artwork.jpg   (needs Pillow and numpy)

Why the pictures are changed, not just resized: the page puts words over them, so the colour is
cut to about half and the brightest parts are capped (a soft knee, so glows stay glowing but
never get bright enough to hurt the contrast of the words on top). With --art-opacity in
global.css, the lightest pixel on the page is still dark enough for muted text to pass 4.5:1.
If you swap the artwork or change CAP or the opacity, re-check that ratio.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

CAP = 76.0  # the brightest a pixel can get (out of 255)
KNEE = 60.0  # how quickly pixels reach the cap
OUT = Path(__file__).resolve().parent.parent / "public" / "art"


def prepare(image: Image.Image) -> Image.Image:
    image = ImageEnhance.Color(image).enhance(0.55)  # partly saturated
    pixels = CAP * (1 - np.exp(-np.asarray(image, float) / KNEE))
    return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))


def main(source: str) -> None:
    art = Image.open(source).convert("RGB")
    width, height = art.size
    OUT.mkdir(parents=True, exist_ok=True)
    prepare(art).save(OUT / "table-wide.webp", "WEBP", quality=70, method=6)
    # The phone crop drops the empty left side of the wide picture and keeps the artwork.
    prepare(art.crop((int(width * 0.436), 0, width, height))).save(
        OUT / "table-tall.webp", "WEBP", quality=70, method=6
    )


if __name__ == "__main__":
    main(sys.argv[1])
