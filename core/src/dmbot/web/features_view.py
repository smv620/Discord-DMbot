"""The campaign settings page's read-only "what's switched on" list (#3693).

The bot knows which features are on (`dmbot.features`, from its own settings and keys). The
website's API must not hold those keys, so the bot writes the finished list, names and plain
sentences only, as one small JSON file on a volume the API reads read-only, and this reads it back.
Nothing here decides what is on, and nothing from the file is trusted beyond its shape: a missing,
old, oversized or oddly shaped file is simply "not available", never an error for the page."""

from __future__ import annotations

import json
import logging
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
    if not text or len(text) > MAX_TEXT or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return None
    return text


def read_features(directory: str) -> dict[str, Any] | None:
    """`{"writtenAt": n, "on": [{"name", "words"}], "off": [name]}` from the bot's file, or None."""
    if not directory:
        return None
    path = Path(directory) / FILE
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    written = raw.get("writtenAt")
    on_raw, off_raw = raw.get("on"), raw.get("off")
    if (
        not isinstance(written, int)
        or isinstance(written, bool)
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
    return {"writtenAt": written, "on": on, "off": [n for n in off if n is not None]}
