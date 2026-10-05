"""Running the bake-off: phases, resumable results, and re-listen.

Results go to `<out>/results.jsonl`, one JSON object per request. A run can be stopped
and started again: finished requests are skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dmbot.devtools.stt_bakeoff import data
from dmbot.devtools.stt_bakeoff.audio import read_wav
from dmbot.devtools.stt_bakeoff.data import LINE_BY_N, NAME_BY_CANONICAL, NAMES, VocabEntry
from dmbot.devtools.stt_bakeoff.providers import Deepgram, Provider, Result, Speechmatics, Whisper
from dmbot.devtools.stt_bakeoff.score import misheard_forms, score

log = logging.getLogger(__name__)

MAIN, CLEAN, LEARNED, RELISTEN, TIMING = "main", "clean", "learned", "relisten", "timing"
PHASES = (MAIN, CLEAN, LEARNED, RELISTEN, TIMING)

# Which word lists each kind of service is tested with (the learned list is its own phase).
SM_LISTS = (data.NONE, data.SCENE, data.SCENE_SL, data.BIG)
DG_LISTS = (data.NONE, data.SCENE, data.BIG)
WHISPER_LISTS = (data.NONE, data.SCENE)

SETUPS: dict[str, tuple[Callable[[], Provider], tuple[str, ...]]] = {
    "sm-standard": (lambda: Speechmatics("standard"), SM_LISTS),
    "sm-enhanced": (lambda: Speechmatics("enhanced"), SM_LISTS),
    "sm-melia": (lambda: Speechmatics("melia-1"), SM_LISTS),
    "dg-nova3": (lambda: Deepgram("nova-3"), DG_LISTS),
    "whisper": (Whisper, WHISPER_LISTS),
}
DEFAULT_SETUPS = ("sm-standard", "sm-enhanced", "dg-nova3", "whisper")
LEARN_FROM = "sm-enhanced"  # whose Scene mistakes become the learned sounds-like hints
CLEAN_SETUPS = ("sm-enhanced", "dg-nova3")
TIMING_SETUPS = ("sm-enhanced", "dg-nova3")
RELISTEN_CANDIDATES = 5


@dataclass(slots=True)
class Record:
    key: str
    phase: str
    setup: str
    wordlist: str
    reader: str
    variant: str
    line: int
    repeat: int
    text: str
    words: list[tuple[str, float | None]]
    connect_s: float
    final_s: float
    audio_s: float
    error: str | None
    note: str
    vocab_size: int
    cold: bool = False
    target: str = ""  # re-listen: the name being re-checked


def key_of(*parts: object) -> str:
    return "|".join(str(p) for p in parts)


class Store:
    def __init__(self, out: Path) -> None:
        self.path = out / "results.jsonl"
        self.done: set[str] = set()
        if self.path.exists():
            for rec in load(self.path):
                if not rec.error:
                    self.done.add(rec.key)

    def add(self, rec: Record) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
        if not rec.error:
            self.done.add(rec.key)


def load(path: Path) -> list[Record]:
    """Every record, keeping only the latest attempt per key."""
    latest: dict[str, Record] = {}
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            if raw.strip():
                d: dict[str, Any] = json.loads(raw)
                d["words"] = [tuple(w) for w in d.get("words", [])]
                latest[d["key"]] = Record(**d)
    return list(latest.values())


@dataclass(frozen=True, slots=True)
class Clip:
    reader: str
    line: int
    variant: str
    path: Path


def clips(out: Path, variant: str) -> Iterator[Clip]:
    manifest = json.loads((out / "manifest.json").read_text())
    for reader, info in sorted(manifest["readers"].items()):
        use = variant if variant in info["variants"] else "clean"
        for line_s, files in sorted(info["lines"].items(), key=lambda kv: int(kv[0])):
            yield Clip(reader, int(line_s), use, out / files[use])


class Runner:
    def __init__(self, out: Path, concurrency: int = 4) -> None:
        self.out = out
        self.store = Store(out)
        self.sem = asyncio.Semaphore(concurrency)
        self._providers: dict[str, Provider] = {}

    def provider(self, setup: str) -> Provider:
        if setup not in self._providers:
            self._providers[setup] = SETUPS[setup][0]()
        return self._providers[setup]

    async def _one(
        self,
        key: str,
        phase: str,
        setup: str,
        wordlist: str,
        clip: Clip,
        vocab: list[VocabEntry],
        *,
        repeat: int = 0,
        realtime: bool = True,
        cold: bool = False,
        target: str = "",
    ) -> None:
        if key in self.store.done:
            return
        async with self.sem:
            res: Result = await self.provider(setup).transcribe(
                read_wav(clip.path), vocab, realtime=realtime
            )
        if res.error:
            log.warning("%s: %s", key, res.error)
        self.store.add(
            Record(
                key,
                phase,
                setup,
                wordlist,
                clip.reader,
                clip.variant,
                clip.line,
                repeat,
                res.text,
                res.words,
                res.connect_s,
                res.final_s,
                res.audio_s,
                res.error,
                res.note,
                len(vocab),
                cold,
                target,
            )
        )

    async def _gather(self, jobs: list[Any]) -> None:
        total = len(jobs)
        started = time.perf_counter()
        for i in range(0, total, 50):
            await asyncio.gather(*jobs[i : i + 50])
            log.info(
                "%d/%d requests (%.0f s)", min(i + 50, total), total, time.perf_counter() - started
            )

    async def main(self, setups: list[str], limit: int | None = None) -> None:
        jobs = []
        for setup in setups:
            for wl in SETUPS[setup][1]:
                vocab = data.word_list(wl)
                for clip in list(clips(self.out, "discord"))[:limit]:
                    k = key_of(MAIN, setup, wl, clip.reader, clip.variant, clip.line)
                    jobs.append(self._one(k, MAIN, setup, wl, clip, vocab))
        await self._gather(jobs)

    async def clean(self, setups: list[str]) -> None:
        jobs = []
        for setup in [s for s in CLEAN_SETUPS if s in setups]:
            vocab = data.word_list(data.SCENE)
            for clip in clips(self.out, "clean"):
                k = key_of(CLEAN, setup, data.SCENE, clip.reader, clip.variant, clip.line)
                jobs.append(self._one(k, CLEAN, setup, data.SCENE, clip, vocab))
        await self._gather(jobs)

    def learned_hints(self, reader: str) -> dict[str, set[str]]:
        """Sounds-like forms learned from the *other* readers' mistakes (so a reader's
        own clips are never used to tune the list they're tested with)."""
        hints: dict[str, set[str]] = {}
        for rec in load(self.store.path):
            if (rec.phase, rec.setup, rec.wordlist) != (MAIN, LEARN_FROM, data.SCENE):
                continue
            if rec.reader == reader or rec.error:
                continue
            for name, heard in misheard_forms(LINE_BY_N[rec.line], rec.words).items():
                hints.setdefault(name, set()).add(heard)
        return hints

    async def learned(self, setups: list[str]) -> None:
        jobs = []
        sm = [s for s in setups if s.startswith("sm-")]
        by_reader: dict[str, list[VocabEntry]] = {}
        for setup in sm:
            for clip in clips(self.out, "discord"):
                if clip.reader not in by_reader:
                    hints = self.learned_hints(clip.reader)
                    by_reader[clip.reader] = data.word_list(data.LEARNED, hints)
                    log.info("Learned hints for %s: %d names", clip.reader, len(hints))
                vocab = by_reader[clip.reader]
                k = key_of(LEARNED, setup, data.LEARNED, clip.reader, clip.variant, clip.line)
                jobs.append(self._one(k, LEARNED, setup, data.LEARNED, clip, vocab))
        await self._gather(jobs)

    async def relisten(self, setups: list[str]) -> None:
        """Re-send clips whose names were missed with the Scene list, with a focused list
        of a few candidates, as fast as possible (the audio already exists)."""
        jobs = []
        recs = [
            r
            for r in load(self.store.path)
            if r.phase == MAIN
            and r.wordlist == data.SCENE
            and r.setup in setups
            and r.setup != "whisper"
            and not r.error
        ]
        paths = {(c.reader, c.line): c for c in clips(self.out, "discord")}
        for rec in recs:
            clip = paths.get((rec.reader, rec.line))
            if clip is None:
                continue
            for missed in score(rec.line, rec.words).missed:
                vocab = focused_list(missed, with_sounds_like=rec.setup.startswith("sm-"))
                k = key_of(RELISTEN, rec.setup, missed, rec.reader, clip.variant, rec.line)
                jobs.append(
                    self._one(
                        k,
                        RELISTEN,
                        rec.setup,
                        "focused",
                        clip,
                        vocab,
                        realtime=False,
                        target=missed,
                    )
                )
        await self._gather(jobs)

    async def timing(self, setups: list[str], lines: int = 20, repeats: int = 3) -> None:
        """Speed on a subset: repeated runs with the Scene list (dictionary cached), and
        'cold' runs where a one-off extra word forces a new dictionary."""
        jobs = []
        base = data.word_list(data.SCENE)
        subset = [c for c in clips(self.out, "discord") if c.line <= lines]
        for setup in [s for s in TIMING_SETUPS if s in setups]:
            for rep in range(repeats):
                for clip in subset:
                    k = key_of(TIMING, setup, "warm", rep, clip.reader, clip.line)
                    jobs.append(self._one(k, TIMING, setup, data.SCENE, clip, base, repeat=rep))
                    nonce = VocabEntry("Zx" + uuid.uuid4().hex[:8])
                    k = key_of(TIMING, setup, "cold", rep, clip.reader, clip.line)
                    jobs.append(
                        self._one(
                            k,
                            TIMING,
                            setup,
                            data.SCENE,
                            clip,
                            [*base, nonce],
                            repeat=rep,
                            cold=True,
                        )
                    )
        await self._gather(jobs)


def focused_list(name: str, with_sounds_like: bool) -> list[VocabEntry]:
    """The missed name plus the closest-spelled other names (at most 5 in all)."""
    from dmbot.devtools.stt_bakeoff.score import align

    target = name.casefold()

    def distance(other: str) -> int:
        dist, _ = align(list(target), list(other.casefold()))
        return dist

    others = sorted(
        (n.canonical for n in NAMES if n.canonical != name and not n.is_word), key=distance
    )
    chosen = [name, *others[: RELISTEN_CANDIDATES - 1]]
    return [
        VocabEntry(c, NAME_BY_CANONICAL[c].sounds_like if with_sounds_like else ()) for c in chosen
    ]
