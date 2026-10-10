"""The saved test sessions as a library (#1019): list them, keep one, delete a person's voice,
clear out the old ones. Pure file work, used by the bot (daily clean-up, Stop saving my voice)
and by `python -m dmbot.devtools.test_library` (list, keep). Nothing here shows an id.
"""

from __future__ import annotations

import shutil
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dmbot.test_recording import files


@dataclass(frozen=True, slots=True)
class Info:
    folder: str
    size: int  # bytes
    speakers: int
    utterances: int
    ended: bool  # the session finished normally
    kept: str | None  # the name it was kept under
    note: str
    incomplete: bool  # a speaker's voice was deleted from a kept session

    @property
    def complete(self) -> bool:
        return self.ended and not self.incomplete


def session_folders(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / files.MANIFEST).exists())


def listing(root: Path) -> list[Info]:
    out: list[Info] = []
    for folder in session_folders(root):
        manifest = files.read_json(folder / files.MANIFEST) or {}
        kept = files.read_json(folder / files.KEPT) or {}
        out.append(
            Info(
                folder.name,
                files.folder_size(folder),
                len(manifest.get("speakers", [])),
                len(manifest.get("utterances", [])),
                bool(manifest.get("ended")),
                kept.get("name"),
                str(kept.get("note", "")),
                bool(kept.get("incomplete")),
            )
        )
    return out


class LibraryError(ValueError):
    """Plain words for whoever ran the command."""


def keep(root: Path, folder: str, name: str, note: str, *, now: datetime | None = None) -> Path:
    """Mark a session to keep under a name, with what passed. It is not deleted after 7 days."""
    target = root / folder
    if Path(folder).name != folder or not (target / files.MANIFEST).exists():
        raise LibraryError(f"No saved session called {folder!r} (see `test_library list`).")
    if not name.strip() or any(c in name for c in "/\\"):
        raise LibraryError("Give the case a short name without slashes.")
    manifest = files.read_json(target / files.MANIFEST) or {}
    if not manifest.get("ended"):
        raise LibraryError("That session did not finish, so it can't be replayed. Not kept.")
    for other in session_folders(root):
        existing = files.read_json(other / files.KEPT)
        if existing and existing.get("name") == name and other != target:
            raise LibraryError(f"A kept case is already called {name!r}.")
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    files.write_json(
        target / files.KEPT,
        {"name": name.strip(), "note": note.strip(), "kept_at": stamp, "incomplete": False},
    )
    return target


def delete_person(
    root: Path, key: bytes, guild_id: int, user_id: int, *, live: Collection[Path] = ()
) -> int:
    """Stop saving my voice: delete this person's files from every session and take them
    out of every manifest. A kept session that lost a speaker is marked incomplete; an unkept
    one with nobody left is deleted. How many sessions had them. `live`: folders of sessions
    running now, which clean up after themselves (TestSession.forget) and are left alone
    here, so two writers never rewrite one manifest."""
    code = files.voice_code(key, guild_id, user_id)
    touched = 0
    for folder in session_folders(root):
        if folder in live:
            continue
        manifest = files.read_json(folder / files.MANIFEST)
        if manifest is None:
            continue
        mine = [s for s in manifest.get("speakers", []) if s.get("voice") == code]
        if not mine:
            continue
        touched += 1
        numbers = {s["speaker"] for s in mine}
        for number in numbers:  # every file of theirs, listed in the manifest or not
            for path in (folder / files.AUDIO).glob(f"*-{number}.flac"):
                path.unlink(missing_ok=True)
        manifest["utterances"] = [u for u in manifest["utterances"] if u["speaker"] not in numbers]
        manifest["speakers"] = [s for s in manifest["speakers"] if s["voice"] != code]
        produced = manifest.get("produced", {})
        for part in ("transcript", "shown"):
            produced[part] = [t for t in produced.get(part, []) if t["speaker"] not in numbers]
        manifest["removed_speakers"] = [
            *manifest.get("removed_speakers", []),
            *({"speaker": s["speaker"], "role": s["role"]} for s in mine),
        ]
        kept = files.read_json(folder / files.KEPT)
        if kept is not None:
            kept["incomplete"] = True
            kept["lost"] = [*kept.get("lost", []), *(s["role"] for s in mine)]
            files.write_json(folder / files.KEPT, kept)
            files.write_json(folder / files.MANIFEST, manifest)
        elif not manifest["speakers"]:
            shutil.rmtree(folder, ignore_errors=True)
        else:
            files.write_json(folder / files.MANIFEST, manifest)
    return touched


def cleanup(root: Path, now: float, *, keep_days: int = files.KEEP_DAYS) -> int:
    """Delete sessions nobody kept that were last written more than `keep_days` ago. How
    many were deleted."""
    gone = 0
    for folder in session_folders(root):
        if (folder / files.KEPT).exists():
            continue
        if now - _newest(folder) > keep_days * 86400:
            shutil.rmtree(folder, ignore_errors=True)
            gone += 1
    return gone


def _newest(folder: Path) -> float:
    """When anything in the folder was last written; a file that vanishes meanwhile (a
    person deleted, a session ending) is skipped."""
    newest = 0.0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def describe(info: Info) -> str:
    """One line for `list`: sizes and names, no ids."""
    state = "complete" if info.complete else ("INCOMPLETE" if info.incomplete else "unfinished")
    kept = f" kept as {info.kept!r}" if info.kept else ""
    note = f" - {info.note}" if info.note else ""
    return (
        f"{info.folder}  {info.size / 1024:.0f} KB  {info.speakers} speaker(s)  "
        f"{info.utterances} utterance(s)  {state}{kept}{note}"
    )


def as_dict(info: Info) -> dict[str, Any]:
    return {
        "folder": info.folder,
        "size": info.size,
        "speakers": info.speakers,
        "utterances": info.utterances,
        "complete": info.complete,
        "kept": info.kept,
        "note": info.note,
    }
