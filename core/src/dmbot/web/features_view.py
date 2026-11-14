"""The campaign settings page's read-only "what's switched on" list (#3693).

The bot knows which features are on (`dmbot.features`, from its own settings and keys). The
website's API must not hold those keys, so the bot writes the finished list, names and plain
sentences only, as one small JSON file on a volume the API reads read-only, and this reads it back.
Nothing here decides what is on, and nothing from the file is trusted beyond its shape: a missing,
old, oversized or oddly shaped file is simply "not available", never an error for the page."""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FILE = "features.json"
MAX_BYTES = 64 * 1024
MAX_ITEMS = 60
MAX_TEXT = 300


def _text(value: object) -> str | None:
    """A short line with no control characters, or None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    # (Cf: zero-width and right-to-left override characters: a line could pass as another.)
    if (
        not text
        or len(text) > MAX_TEXT
        or any(ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) == "Cf" for c in text)
    ):
        return None
    return text


def read_features(directory: str) -> dict[str, Any] | None:
    """`{"writtenAt": n, "on": [{"name", "words"}], "off": [name]}` from the bot's file, or None."""
    if not directory:
        return None
    path = Path(directory) / FILE
    try:
        # Never follow a link out of the volume, and never read more than the limit even if the file
        # grows while it is read.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        raw = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    written = raw.get("writtenAt")
    on_raw, off_raw = raw.get("on"), raw.get("off")
    if (
        not isinstance(written, int)
        or isinstance(written, bool)
        or not 0 < written < 10**11  # (a sane time in seconds: the page shows it as a date)
        or not isinstance(on_raw, list)
        or not isinstance(off_raw, list)
        or len(on_raw) > MAX_ITEMS
        or len(off_raw) > MAX_ITEMS
    ):
        return None
    on: list[dict[str, str]] = []
    for item in on_raw:
        name = _text(item.get("name")) if isinstance(item, dict) else None
        words = _text(item.get("words")) if isinstance(item, dict) else None
        if name is None or words is None:
            return None
        on.append({"name": name, "words": words})
    off = [_text(n) for n in off_raw]
    if any(n is None for n in off):
        return None
    # A name appears once, so the page's keys are unique.
    if len({d["name"] for d in on}) != len(on) or len(set(off)) != len(off):
        return None
    return {"writtenAt": written, "on": on, "off": [n for n in off if n is not None]}
