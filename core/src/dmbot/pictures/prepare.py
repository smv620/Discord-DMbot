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
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 40_000_000  # about 40 megapixels
MAX_BYTES = 25 * 1024 * 1024  # Discord's own upload limit
MAX_FRAMES = 200  # a GIF with more frames than this is refused
PREVIEW_PX = 400
GIF_PIXELS = 60_000_000  # width x height x frames of a GIF: the most that is held in memory
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow warns past this and raises past twice it
# Our own check below is the exact one and runs before anything is decoded, so Pillow's warning
# between one and two times the limit is only noise.
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

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


def _clean(image: Image.Image) -> Image.Image:
    """The same pixels in a new image that carries nothing else. A copy keeps `info` (an ICC
    profile, a GIF comment, XMP) and the writers fall back to it, so a fresh image is the only way
    to be sure nothing rides along."""
    if image.mode in ("P", "PA"):
        image = image.convert(
            "RGBA" if "transparency" in image.info or "A" in image.mode else "RGB"
        )
    return Image.frombytes(image.mode, image.size, image.tobytes())


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
            if frames > MAX_FRAMES or width * height * frames > GIF_PIXELS:
                raise Refused("frames")  # every frame is held in memory at once
            durations = []
            pictures = []
            for n in range(frames):
                image.seek(n)
                durations.append(int(image.info.get("duration", 100)))
                pictures.append(_clean(image.convert("RGBA")))
            out = io.BytesIO()
            pictures[0].save(
                out,
                "GIF",
                save_all=True,
                append_images=pictures[1:],
                duration=durations,
                loop=0,
                comment=None,
            )
            return Prepared(out.getvalue(), "gif", _preview(pictures[0]), sha, width, height)
        image.load()
        upright = _clean(ImageOps.exif_transpose(image))
    except Refused:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Refused("pixels") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError, MemoryError) as exc:
        raise Refused(type(exc).__name__) from None
    fmt, ext = _KEPT.get(source, ("PNG", "png"))
    if source == "GIF":
        fmt, ext = "PNG", "png"  # a still GIF is kept as a PNG: same pixels, no GIF metadata
    try:
        return Prepared(_save(upright, fmt), ext, _preview(upright), sha, *upright.size)
    except (OSError, ValueError, MemoryError) as exc:
        raise Refused(type(exc).__name__) from None


async def prepare_async(data: bytes) -> Prepared:
    """`prepare` off the event loop."""
    return await asyncio.to_thread(prepare, data)
