"""One saved test session (#1019): the audio of people who agreed, and the manifest.

The manifest names no one: speakers are made-up numbers (1001 the DM, 1002 and up players)
with a role label and a voice code (files.voice_code), times are milliseconds on one session
clock, and what DMbot produced (transcript lines, sidebar answers) is plain text.

A speaker's number is never given to anyone else in the session, even after they are
deleted, so the consent events and removed speakers always point at one person.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dmbot.test_recording import files, flac

FLUSH_EVERY_S = 5.0  # the manifest is rewritten at most this often while utterances come in


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
        self._numbers: dict[int, int] = {}  # account -> made-up speaker number
        self._registered: set[int] = set()  # accounts with a speakers[] entry
        self._next = files.DM_SPEAKER + 1
        self._dm_taken = False
        self._wrote = 0
        self._gaps = 0
        self._flush_task: asyncio.Task[None] | None = None
        files.ensure_root(root)
        (self.folder / files.AUDIO).mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.flush()

    # ---- who ----------------------------------------------------------------------

    def _number(self, user_id: int, is_dm: bool) -> int:
        """This person's made-up speaker number, reserved on first use and never reused."""
        if user_id not in self._numbers:
            if is_dm and not self._dm_taken:
                self._dm_taken = True
                self._numbers[user_id] = files.DM_SPEAKER
            else:
                self._numbers[user_id] = self._next
                self._next += 1
        return self._numbers[user_id]

    def _register(self, user_id: int) -> int:
        """Put the speaker in the manifest, once they have something in it."""
        number = self._numbers[user_id]
        if user_id not in self._registered:
            self._registered.add(user_id)
            role = "DM" if number == files.DM_SPEAKER else f"Player {number - files.DM_SPEAKER}"
            self._manifest["speakers"].append(
                {
                    "speaker": number,
                    "role": role,
                    "voice": files.voice_code(self._key, self._guild_id, user_id),
                }
            )
        return number

    def has(self, user_id: int) -> bool:
        return user_id in self._registered

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
        number = self._number(user_id, is_dm)
        self._wrote += 1
        name = f"{files.AUDIO}/{self._wrote:04d}-{number}.flac"
        path = self.folder / name
        await asyncio.to_thread(_write, path, data)
        if not allowed() or self._numbers.get(user_id) != number:
            path.unlink(missing_ok=True)  # stopped (or stopped and started again) meanwhile
            return False
        self._register(user_id)
        self._manifest["utterances"].append(
            {
                "file": name,
                "speaker": number,
                "start_ms": self.clock(start_ms),
                "end_ms": self.clock(end_ms),
            }
        )
        self.line(user_id, start_ms, text)
        self._flush_soon()
        return True

    def gap(self) -> None:
        """An utterance was not saved (too many waiting): the replay knows it is incomplete."""
        self._gaps += 1
        self._manifest["gaps"] = self._gaps

    def line(self, user_id: int, unix_ms: int, text: str | None) -> None:
        """What DMbot wrote down for a saved speaker (the cleaned words)."""
        if text and user_id in self._registered:
            self._manifest["produced"]["transcript"].append(
                {"at_ms": self.clock(unix_ms), "speaker": self._numbers[user_id], "text": str(text)}
            )

    def shown(self, user_id: int, kind: str, unix_ms: int, text: str) -> None:
        """Something DMbot showed or answered for a saved speaker (a sidebar question, say), as
        plain text. Nothing is kept for someone who did not say yes to saving."""
        if user_id not in self._registered:
            return
        self._manifest["produced"]["shown"].append(
            {
                "at_ms": self.clock(unix_ms),
                "speaker": self._numbers[user_id],
                "kind": kind,
                "text": text,
            }
        )
        self._flush_soon()

    def stopped(self, user_id: int, unix_ms: int) -> None:
        """A speaker pressed Stop saving (or Stop recording me): a consent event at time T."""
        if user_id in self._registered:
            self._manifest["consent_events"].append(
                {
                    "at_ms": self.clock(unix_ms),
                    "speaker": self._numbers[user_id],
                    "event": "stopped saving",
                }
            )
            self._flush_soon()

    def finish(self, extra: dict[str, Any] | None = None) -> None:
        """The session is over: in time order, and written out at once."""
        m = self._manifest
        m["utterances"].sort(key=lambda u: (u["start_ms"], u["speaker"]))
        m["produced"]["transcript"].sort(key=lambda t: t["at_ms"])
        m["produced"]["shown"].sort(key=lambda t: t["at_ms"])
        m["ended"] = True
        m.update(extra or {})
        self.flush()

    def forget(self, user_id: int) -> None:
        """Drop everything of this person from the running session (the files too): the
        same cleanup `library.delete_person` does for finished ones. Their number is not
        given to anyone else."""
        number = self._numbers.get(user_id)
        if number is None:
            return
        m = self._manifest
        # At once, before anything that waits: from here no utterance of theirs is kept.
        self._numbers.pop(user_id, None)
        self._registered.discard(user_id)
        for path in (self.folder / files.AUDIO).glob(f"*-{number}.flac"):  # also unlisted ones
            path.unlink(missing_ok=True)
        m["utterances"] = [u for u in m["utterances"] if u["speaker"] != number]
        for part in ("transcript", "shown"):
            m["produced"][part] = [t for t in m["produced"][part] if t["speaker"] != number]
        removed = next((s for s in m["speakers"] if s["speaker"] == number), None)
        m["speakers"] = [s for s in m["speakers"] if s["speaker"] != number]
        if removed is not None:
            m.setdefault("removed_speakers", []).append(
                {"speaker": number, "role": removed["role"]}
            )
        self._flush_soon()

    # ---- the manifest on disk ------------------------------------------------------

    def flush(self) -> None:
        """Write the manifest now (the loop is held for it: used at the start and the end)."""
        files.write_text(self.folder / files.MANIFEST, self._text())

    def _text(self) -> str:
        return json.dumps(self._manifest, indent=2, sort_keys=True)

    def _flush_soon(self) -> None:
        """Rewrite the manifest in a few seconds, off the event loop, however many
        utterances arrive meanwhile: a long session's manifest is not rewritten for each."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            self._flush_task = asyncio.get_running_loop().create_task(self._flush_later())
        except RuntimeError:  # no loop (a plain script): write at once
            self.flush()

    async def _flush_later(self) -> None:
        await asyncio.sleep(FLUSH_EVERY_S)
        text = self._text()  # on the loop, so nothing changes under it
        with contextlib.suppress(OSError):  # the next change, or the end, writes it again
            await asyncio.to_thread(files.write_text, self.folder / files.MANIFEST, text)


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)
