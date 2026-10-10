"""Makes the two background pictures in public/art/ from the owner's artwork (#background).

Usage: python3 scripts/make-art.py path/to/artwork.jpg   (needs Pillow and numpy)

Why the pictures are changed, not just resized: the page puts words over them, so the colour is
cut to about half and the brightest parts are capped (a soft knee, so glows stay glowing but
never get bright enough to hurt the words on top). The dice are the exception: they are found
by colour and lifted so they stand out. Words sit on cards and on the faded left side, and
the dice are far from where bare text goes, so the lift doesn't hurt reading. If you swap the
artwork or change CAP, DICE_CAP or --art-opacity, look at the pages again.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

CAP = 128.0  # the brightest a pixel can get (out of 255)
KNEE = 80.0  # how quickly pixels reach the cap
DICE_CAP = 235.0  # the dice are the part people look for, so they may get much brighter
DICE_LIFT = 2.4  # how much brighter the dice are made before the cap
OUT = Path(__file__).resolve().parent.parent / "public" / "art"


def dice_mask(image: Image.Image) -> np.ndarray:
    """0..1 mask of the green glowing dice on the table (found by colour; they are small,
    saturated green shapes in the lower right), grown and softened so there is a glow."""
    hsv = np.asarray(image.convert("HSV"), float)
    hue, sat, val = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    found = (hue > 95) & (hue < 175) & (sat > 0.30) & (val > 0.22)
    height, width = found.shape
    found[: int(height * 0.515)] = False  # the green mist and books above the table
    found[:, : int(width * 0.51)] = False  # the empty left side
    mask = Image.fromarray((found * 255).astype("uint8")).filter(ImageFilter.MaxFilter(9))
    mask = mask.filter(ImageFilter.GaussianBlur(7))
    return np.clip(np.asarray(mask, float) / 255 * 2.2, 0, 1)


def prepare(image: Image.Image, dice: np.ndarray) -> Image.Image:
    toned = ImageEnhance.Color(image).enhance(0.55)  # partly saturated
    plain = CAP * (1 - np.exp(-np.asarray(toned, float) / KNEE))
    bright = DICE_CAP * (1 - np.exp(-np.asarray(image, float) * DICE_LIFT / KNEE / 1.3))
    weight = dice[..., None]
    return Image.fromarray(np.clip(plain * (1 - weight) + bright * weight, 0, 255).astype("uint8"))


def main(source: str) -> None:
    art = Image.open(source).convert("RGB")
    width, height = art.size
    dice = dice_mask(art)
    OUT.mkdir(parents=True, exist_ok=True)
    prepare(art, dice).save(OUT / "table-wide.webp", "WEBP", quality=72, method=6)
    # The phone crop drops the empty left side of the wide picture and keeps the artwork.
    left = int(width * 0.436)
    prepare(art.crop((left, 0, width, height)), dice[:, left:]).save(
        OUT / "table-tall.webp", "WEBP", quality=72, method=6
    )


if __name__ == "__main__":
    main(sys.argv[1])
