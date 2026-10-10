"""Where test recordings live and how they are named (#1019). Pure file work: no Discord,
no database. One folder per session under the recordings folder, owner-only (mode 0700):

    YYYY-MM-DD-HHMM-<short id>/
        session.json   who spoke (made-up speaker numbers), when, what DMbot made of it
        audio/         one FLAC file per utterance
        kept.json      only when someone marked the session to keep (see library.py)

A person is known to the files only by a *voice code*: a keyed hash of their server and
account, made with a secret that lives in the recordings folder and nowhere else. So the
files hold no Discord id, name or server, and "Stop saving my voice" can still find every
file of one person across sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

KEY_FILE = ".key"
MANIFEST = "session.json"
KEPT = "kept.json"
AUDIO = "audio"
DM_SPEAKER = 1001  # made-up speaker numbers: 1001 for the DM, 1002 and up for players
KEEP_DAYS = 7
FORMAT = 1


def ensure_root(root: Path) -> None:
    """The recordings folder, owner-only."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)


def load_key(root: Path) -> bytes:
    """The secret for voice codes, made on first use (owner-only), kept with the files."""
    ensure_root(root)
    path = root / KEY_FILE
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(secrets.token_bytes(32))
    return path.read_bytes()


def voice_code(key: bytes, guild_id: int, user_id: int) -> str:
    """`v-` and 12 letters and digits that stand for one person in one server."""
    digest = hmac.new(key, f"{guild_id}:{user_id}".encode(), hashlib.sha256).hexdigest()
    return "v-" + digest[:12]


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Replace a file whole or not at all (a crash never leaves half a manifest)."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def folder_size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


_SHA = re.compile(r"[0-9a-f]{4,40}")


def short_commit(value: str | None) -> str | None:
    """The code's commit id (GIT_COMMIT), shortened, or None: nothing else is written down."""
    value = (value or "").strip().casefold()
    return value[:7] if _SHA.fullmatch(value) else None
