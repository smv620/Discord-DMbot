"""Getting a picture ready to keep (#3274): metadata gone, and a small preview.

A picture someone posts may carry their location (GPS in EXIF), the camera, the time and a colour
profile. Before anything is stored it is **re-encoded from its pixels**: opened, turned upright by
its orientation tag, and saved fresh with no EXIF and no profile, so there is no tag left to miss
(owner / Supervisor ruling, 2026-11-02). It keeps its format (JPEG, PNG, WebP, GIF); an odd one
(BMP, TIFF) becomes a PNG. A picture of more than `MAX_PIXELS` is refused before it is decoded (a
small file can hold a huge image), as is anything that will not decode: the caller then describes
the picture without keeping it and logs only the error class. The preview is a WebP of at most
`PREVIEW_PX` on its long side. The work is done in a thread so the bot is never held up.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 40_000_000  # about 40 megapixels
MAX_BYTES = 25 * 1024 * 1024  # Discord's own upload limit
MAX_FRAMES = 200  # a GIF with more frames than this is refused
PREVIEW_PX = 400
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow warns past this and raises past twice it

_KEPT = {"JPEG": ("JPEG", "jpg"), "PNG": ("PNG", "png"), "WEBP": ("WEBP", "webp")}


class Refused(Exception):
    """The picture cannot be kept. Only a short reason, never the picture's own words."""


@dataclass(frozen=True, slots=True)
class Prepared:
    data: bytes  # the picture, re-encoded, no metadata
    ext: str  # jpg, png, webp or gif
    preview: bytes  # WebP, at most PREVIEW_PX on its long side
    sha256: str  # of the bytes posted, so the same file is the same picture
    width: int
    height: int


def _save(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(out, "JPEG", quality=92, optimize=True)
    elif fmt == "WEBP":
        image.save(out, "WEBP", quality=90)
    else:
        image.save(out, "PNG", optimize=True)
    return out.getvalue()


def _preview(image: Image.Image) -> bytes:
    small = image.copy()
    small.thumbnail((PREVIEW_PX, PREVIEW_PX))
    if small.mode not in ("RGB", "RGBA"):
        small = small.convert(
            "RGBA" if "A" in small.mode or "transparency" in small.info else "RGB"
        )
    out = io.BytesIO()
    small.save(out, "WEBP", quality=80)
    return out.getvalue()


def prepare(data: bytes) -> Prepared:
    """Strip and preview a picture, or raise `Refused`."""
    if not data or len(data) > MAX_BYTES:
        raise Refused("size")
    sha = hashlib.sha256(data).hexdigest()
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size  # read from the header: nothing is decoded yet
        if width * height > MAX_PIXELS:
            raise Refused("pixels")
        source = (image.format or "").upper()
        animated = source == "GIF" and getattr(image, "n_frames", 1) > 1
        if animated:
            frames = getattr(image, "n_frames", 1)
            if frames > MAX_FRAMES:
                raise Refused("frames")
            out = io.BytesIO()
            first = image.convert("RGBA")
            rest = []
            for n in range(1, frames):
                image.seek(n)
                rest.append(image.convert("RGBA"))
            first.save(out, "GIF", save_all=True, append_images=rest, loop=0)
            image.seek(0)
            return Prepared(out.getvalue(), "gif", _preview(first), sha, first.width, first.height)
        image.load()
        upright = ImageOps.exif_transpose(image)
    except Refused:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Refused("pixels") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
        raise Refused(type(exc).__name__) from None
    fmt, ext = _KEPT.get(source, ("PNG", "png"))
    if source == "GIF":
        fmt, ext = "PNG", "png"  # a still GIF is kept as a PNG: same pixels, no GIF metadata
    try:
        return Prepared(_save(upright, fmt), ext, _preview(upright), sha, *upright.size)
    except (OSError, ValueError) as exc:
        raise Refused(type(exc).__name__) from None


async def prepare_async(data: bytes) -> Prepared:
    """`prepare` off the event loop."""
    return await asyncio.to_thread(prepare, data)
