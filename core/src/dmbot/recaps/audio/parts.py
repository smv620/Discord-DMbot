"""What is kept beside a recap's audio so a small change to its words can remake only the voice
(#2630): each paragraph's speech, the music already made, and a manifest that says which words each
speech was made from.

When the DM edits a finished recap, `classify` compares the new script with the manifest. If the
paragraphs and the mood cues are the same, the spoken text is within 10% of its old length and no
paragraph was added or removed, only the paragraphs whose words changed go to the voice again; the
rest reuse their saved speech, and the music and ambience already made are kept (the mix is made
again, fitted to the new length). A bigger change remakes everything, and `Change.why` is a fixed
code the page puts into words.

Nothing here is sent to anyone. The manifest holds a hash of each paragraph's tagged text, never
the words; the speech files are the narrator's own audio. Like the rest of the audio, these files
are not part of a backup and go with the folder (180 days, or the campaign).
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.recaps.audio.script import Script

WORK = "work"  # inside a recap's audio folder; replaced with it, atomically
MANIFEST = "manifest.json"
MUSIC_PIECE = "music.mp3"  # the composed music as made, before the mix fits it
VERSION = 1
SMALL = 0.10  # a change of more than this share of the spoken text's length is not small

# Why everything is made again, as fixed words (the page words them).
NO_PARTS = "no_parts"  # nothing was kept (audio made before this, or a music-only recap)
PARAGRAPHS = "paragraphs"  # a paragraph was added or removed
MOODS = "moods"  # a mood cue or a pacing cue changed, so the music no longer follows the story
SIZE = "size"  # the spoken text changed by more than a tenth
VOICE, ALL, SAME, NEW = "voice", "all", "same", "new"  # NEW: nothing was made before


@dataclass(frozen=True, slots=True)
class Change:
    """What a changed text asks of the audio: `voice` (only `changed` paragraphs are spoken
    again), `all` (everything is made again, `why` says why) or `same` (the words the voice reads
    are the same: nothing is spoken again)."""

    kind: str
    why: str = ""
    changed: tuple[int, ...] = ()
    total: int = 0


def digest(tagged: str) -> str:
    return hashlib.sha256(tagged.encode()).hexdigest()


def manifest_of(
    script: Script,
    *,
    mode: str,
    seed: int,
    seconds: list[float],
    cue_at: list[list[float]],
) -> dict[str, Any]:
    """The manifest for a script whose paragraph `i` took `seconds[i]` to say, with its mood cues
    beginning `cue_at[i]` seconds into it."""
    return {
        "version": VERSION,
        "mode": mode,
        "seed": seed,
        "paragraphs": [
            {
                "hash": digest(p.tagged),
                "chars": len(p.spoken),
                "cues": [[c.kind, c.name] for c in p.cues],
                "seconds": seconds[i],
                "cueAt": cue_at[i],
            }
            for i, p in enumerate(script.paragraphs)
        ],
    }


def read_manifest(folder: Path) -> dict[str, Any] | None:
    """The kept manifest of a recap's audio folder, or None when there is none or it is not
    one this code understands."""
    try:
        data = json.loads((folder / WORK / MANIFEST).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    if not isinstance(data.get("paragraphs"), list):
        return None
    return data


def classify(manifest: dict[str, Any] | None, script: Script, *, mode: str, seed: int) -> Change:
    """Compare a new script with what the audio was made from."""
    total = len(script.paragraphs)
    if manifest is None or manifest.get("mode") != mode or manifest.get("seed") != seed:
        return Change(ALL, NO_PARTS, total=total)
    old = manifest["paragraphs"]
    if len(old) != total:
        return Change(ALL, PARAGRAPHS, total=total)
    for before, now in zip(old, script.paragraphs, strict=True):
        if before["cues"] != [[c.kind, c.name] for c in now.cues]:
            return Change(ALL, MOODS, total=total)
    was = sum(int(p["chars"]) for p in old)
    now_chars = sum(len(p.spoken) for p in script.paragraphs)
    if was <= 0 or abs(now_chars - was) > SMALL * was:
        return Change(ALL, SIZE, total=total)
    changed = tuple(
        i for i, (before, now) in enumerate(zip(old, script.paragraphs, strict=True))
        if before["hash"] != digest(now.tagged)
    )  # fmt: skip
    return Change(VOICE if changed else SAME, changed=changed, total=total)


def speech_path(folder: Path, number: int) -> Path:
    return folder / WORK / f"speech-{number:03d}.pcm.gz"


def save_speech(folder: Path, number: int, pcm: bytes) -> None:
    path = speech_path(folder, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(pcm, compresslevel=1))


def load_speech(folder: Path, number: int) -> bytes | None:
    try:
        return gzip.decompress(speech_path(folder, number).read_bytes())
    except (OSError, EOFError):
        return None


def save_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    path = folder / WORK / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
