"""One saved test session (#1019): the audio of people who agreed, and the manifest.

The manifest names no one: speakers are made-up numbers (1001 the DM, 1002 and up players)
with a role label and a voice code (files.voice_code), times are milliseconds on one session
clock, and what DMbot produced (transcript lines, sidebar answers) is plain text.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dmbot.test_recording import files, flac


class TestSession:
    """Kept in memory while the session runs; the manifest is rewritten as it grows."""

    __test__ = False  # not a test class, whatever pytest thinks of the name

    def __init__(
        self,
        root: Path,
        key: bytes,
        guild_id: int,
        started_at: int,
        *,
        settings: dict[str, Any],
        short_id: str | None = None,
    ) -> None:
        self.root = root
        self._key = key
        self._guild_id = guild_id
        self.started_ms = started_at * 1000
        when = datetime.fromtimestamp(started_at, UTC)
        self.folder = root / f"{when:%Y-%m-%d-%H%M}-{short_id or secrets.token_hex(3)}"
        self._manifest: dict[str, Any] = {
            "format": files.FORMAT,
            "started": f"{when:%Y-%m-%d %H:%M} UTC",
            "settings": settings,
            "speakers": [],
            "utterances": [],
            "consent_events": [],
            "produced": {"transcript": [], "shown": []},
            "ended": False,
        }
        self._speakers: dict[int, int] = {}  # account -> made-up speaker number
        self._wrote = 0
        files.ensure_root(root)
        (self.folder / files.AUDIO).mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.flush()

    # ---- who ----------------------------------------------------------------------

    def speaker(self, user_id: int, *, is_dm: bool) -> int:
        """This person's made-up speaker number, given on first use."""
        if user_id not in self._speakers:
            taken = set(self._speakers.values())
            if is_dm and files.DM_SPEAKER not in taken:
                number = files.DM_SPEAKER
            else:
                number = files.DM_SPEAKER + 1
                while number in taken:
                    number += 1
            self._speakers[user_id] = number
            role = "DM" if number == files.DM_SPEAKER else f"Player {number - files.DM_SPEAKER}"
            self._manifest["speakers"].append(
                {
                    "speaker": number,
                    "role": role,
                    "voice": files.voice_code(self._key, self._guild_id, user_id),
                }
            )
        return self._speakers[user_id]

    def has(self, user_id: int) -> bool:
        return user_id in self._speakers

    # ---- what ---------------------------------------------------------------------

    def clock(self, unix_ms: int) -> int:
        """Milliseconds on the session clock."""
        return max(0, unix_ms - self.started_ms)

    async def add_utterance(
        self,
        user_id: int,
        *,
        is_dm: bool,
        start_ms: int,
        end_ms: int,
        pcm: bytes,
        text: str | None,
        allowed: Callable[[], bool],
    ) -> bool:
        """Save one utterance if `allowed()` says the person still agrees: checked before
        anything is encoded, again before the file is written, and again after (a stop
        pressed meanwhile removes the file). True if it was kept."""
        if not allowed():
            return False
        data = await asyncio.to_thread(flac.encode, pcm)
        if not allowed():
            return False
        speaker = self.speaker(user_id, is_dm=is_dm)
        self._wrote += 1
        name = f"{files.AUDIO}/{self._wrote:04d}-{speaker}.flac"
        path = self.folder / name
        await asyncio.to_thread(_write, path, data)
        if not allowed():
            path.unlink(missing_ok=True)
            return False
        self._manifest["utterances"].append(
            {
                "file": name,
                "speaker": speaker,
                "start_ms": self.clock(start_ms),
                "end_ms": self.clock(end_ms),
            }
        )
        self.line(user_id, start_ms, text)
        self.flush()
        return True

    def line(self, user_id: int, unix_ms: int, text: str | None) -> None:
        """What DMbot wrote down for a saved speaker (the cleaned words)."""
        if text and user_id in self._speakers:
            self._manifest["produced"]["transcript"].append(
                {
                    "at_ms": self.clock(unix_ms),
                    "speaker": self._speakers[user_id],
                    "text": str(text),
                }
            )

    def shown(self, kind: str, unix_ms: int, text: str) -> None:
        """Something DMbot showed or answered, as plain text ("sidebar question", ...)."""
        self._manifest["produced"]["shown"].append(
            {"at_ms": self.clock(unix_ms), "kind": kind, "text": text}
        )
        self.flush()

    def stopped(self, user_id: int, unix_ms: int) -> None:
        """A speaker pressed Stop saving (or Stop recording me): a consent event at time T."""
        if user_id in self._speakers:
            self._manifest["consent_events"].append(
                {
                    "at_ms": self.clock(unix_ms),
                    "speaker": self._speakers[user_id],
                    "event": "stopped saving",
                }
            )
            self.flush()

    def finish(self, extra: dict[str, Any] | None = None) -> None:
        self._manifest["ended"] = True
        self._manifest.update(extra or {})
        self.flush()

    def flush(self) -> None:
        files.write_json(self.folder / files.MANIFEST, self._manifest)

    def forget(self, user_id: int) -> None:
        """Drop everything of this person from the running session (the files too): the
        same cleanup `library.delete_person` does for finished ones."""
        number = self._speakers.pop(user_id, None)
        if number is None:
            return
        m = self._manifest
        for item in m["utterances"]:
            if item["speaker"] == number:
                (self.folder / item["file"]).unlink(missing_ok=True)
        m["utterances"] = [u for u in m["utterances"] if u["speaker"] != number]
        m["produced"]["transcript"] = [
            t for t in m["produced"]["transcript"] if t["speaker"] != number
        ]
        removed = next((s for s in m["speakers"] if s["speaker"] == number), None)
        m["speakers"] = [s for s in m["speakers"] if s["speaker"] != number]
        if removed is not None:
            m.setdefault("removed_speakers", []).append(
                {"speaker": number, "role": removed["role"]}
            )
        self.flush()


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def now_ms() -> int:
    return int(time.time() * 1000)
