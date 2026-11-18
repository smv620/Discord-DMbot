"""The pictures in an uploaded PDF, taken out for reading (#4003 slice a; Supervisor's ruling on
#4003, 2026-11-18). Runs inside the document reader's child process (`document_reader`), beside
the text read: no AI, no database, no network.

What counts as a picture worth reading, and the limits:
- **Embedded images only.** pypdf's `page.images`. A map drawn as vector shapes has nothing to
  take out without a page renderer, so it is not read (ruling D); a scanned page is an image, so
  it is.
- **Big enough to hold something:** at least `MIN_SIDE` pixels each way and `MIN_BYTES` as stored,
  which leaves out icons, rules, borders and ornaments.
- **At most `PER_PAGE` a page**, the largest first; **the same image twice is read once** (sha256);
  **at most `PER_UPLOAD`** in page order (ruling C). The rest are counted, never read.

Each picture is written to the caller's private folder as `NNNN-pPPP.png` or `.jpg`, re-encoded by
Pillow (which drops the file's metadata) and no larger than `KEEP_SIDE` on its long side. Only
counts come back through the reply (`document_reader`'s 1 MB cap and bytes-only protocol are
unchanged); the parent lists the folder (`PictureFiles`). The bytes never reach a log.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_SIDE = 300  # pixels, each way
MIN_BYTES = 20_000  # as stored in the PDF (the image's own encoded stream)
MAX_PIXELS = 8 * 2400 * 2400  # a picture bigger than this is refused as a decompression bomb
PER_PAGE = 4
PER_UPLOAD = 30
KEEP_SIDE = 2400  # the longest side written (the AI gets a smaller copy still, slice b)
JPEG_QUALITY = 90
# Where each upload's folder lives. At start-up nothing can be using one, so every folder there is
# a leftover from a stop mid-read and goes (`sweep`).
ROOT = Path(tempfile.gettempdir()) / "dmbot-pictures"
_NAME = re.compile(r"(\d{4})-p(\d{3,})\.(png|jpg)")
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Taken:
    """What a document gave: pictures written to the folder, and those over the limit."""

    found: int = 0
    over: int = 0


BUDGET_S = 10.0  # the most the pictures may take in the child, after the text (`document_reader`)


def take(reader: Any, out_dir: str, *, budget_s: float = BUDGET_S) -> Taken:
    """Write the PDF's pictures worth reading into `out_dir` (made by the parent, empty). In the
    child process; never raises for one bad picture (it is skipped).

    Each image is first judged from its stored object alone (its size in pixels and its stored
    bytes), never decoded; only the ones chosen are decoded, one at a time. Past the upload's
    limit, or once `budget_s` is spent, the rest are counted and never decoded: a slow picture
    step must never cost the text, which is read first and is the reason for the upload."""
    from PIL import Image

    # (a decompression bomb fails at once, caught as one bad picture, not at the memory limit;
    # put back afterwards, as a test calls this in the bot's own process)
    before, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, MAX_PIXELS
    try:
        return _take(reader, Path(out_dir), budget_s)
    finally:
        Image.MAX_IMAGE_PIXELS = before


def _take(reader: Any, out_dir: Path, budget_s: float) -> Taken:
    deadline = time.monotonic() + budget_s
    seen: set[str] = set()
    written = over = 0
    for number, page in enumerate(reader.pages, start=1):
        picked = []
        try:
            images = page.images
            keys = list(images.keys())
        except Exception:  # (an unusual page: its pictures are skipped, its text stands)
            continue
        for key in keys:
            stored = _stored(page, key)
            if stored is None:
                continue
            width, height, data = stored
            if min(width, height) < MIN_SIDE or len(data) < MIN_BYTES:
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            picked.append((width * height, key))
        picked.sort(key=lambda p: -p[0])
        over += max(0, len(picked) - PER_PAGE)  # (more than a page's share: counted, not read)
        for _area, key in picked[:PER_PAGE]:
            if written >= PER_UPLOAD or time.monotonic() > deadline:
                over += 1
                continue
            try:
                picture = images[key].image  # (decodes this one picture only)
            except Exception:
                continue
            if picture is not None and _write(picture, out_dir, written + 1, number):
                written += 1
    return Taken(written, over)


def _stored(page: Any, key: Any) -> tuple[int, int, bytes] | None:
    """An image's width, height and stored (still encoded) bytes, read from its object without
    decoding it. `key` is pypdf's: a name on the page, or the path of names through the forms
    that hold it. An inline image (drawn in the page's own stream) is too small to matter here
    and is None, as is anything that is not an image."""
    try:
        names = [key] if isinstance(key, str) else list(key)
        holder = page
        obj = None
        for name in names:
            obj = holder["/Resources"]["/XObject"][name].get_object()
            holder = obj
        if obj is None or obj.get("/Subtype") != "/Image":
            return None
        return int(obj["/Width"]), int(obj["/Height"]), bytes(obj._data)
    except Exception:
        return None


def _write(picture: Any, out_dir: Path, n: int, page: int) -> bool:
    """One picture, re-encoded no larger than KEEP_SIDE: a photo-like one as JPEG, one with
    transparency or a palette as PNG."""
    try:
        picture.thumbnail((KEEP_SIDE, KEEP_SIDE))
        if picture.mode in ("RGBA", "LA", "P", "PA"):
            kind, picture = "png", picture.convert("RGBA")
        else:
            kind, picture = "jpg", picture.convert("RGB")
        out = io.BytesIO()
        if kind == "png":
            picture.save(out, "PNG", optimize=True)
        else:
            picture.save(out, "JPEG", quality=JPEG_QUALITY)
        (out_dir / f"{n:04d}-p{page:03d}.{kind}").write_bytes(out.getvalue())
        return True
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class PictureFile:
    page: int
    path: Path


class PictureFiles:
    """One upload's private folder of pictures, from the reading child until the read is done or
    the offer is dropped. `close` removes it; a folder left by a stop is removed by `sweep`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def new(cls) -> PictureFiles:
        """A fresh private folder (0700) under ROOT, which is made private too and must be a real
        folder, never a link (the system's temp folder may be shared)."""
        ROOT.mkdir(mode=0o700, exist_ok=True)
        if ROOT.is_symlink() or not ROOT.is_dir():
            raise OSError("the pictures folder is not a private folder")
        ROOT.chmod(0o700)
        return cls(Path(tempfile.mkdtemp(prefix="u-", dir=ROOT)))

    def files(self) -> list[PictureFile]:
        """The pictures written, in page order. Only names the child writes are taken."""
        found = []
        with contextlib.suppress(OSError):
            for entry in sorted(os.listdir(self.path)):
                named = _NAME.fullmatch(entry)
                if named is not None and (self.path / entry).is_file():
                    found.append(PictureFile(int(named.group(2)), self.path / entry))
        return found

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def sweep() -> int:
    """At start-up: remove every upload's folder left by a stop mid-read (nothing can be using
    one yet). Returns how many went."""
    gone = 0
    with contextlib.suppress(OSError):
        if ROOT.is_symlink():
            return 0
        for entry in ROOT.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
                gone += int(not entry.exists())
    return gone


__all__ = ["PER_UPLOAD", "PictureFile", "PictureFiles", "Taken", "sweep", "take"]
