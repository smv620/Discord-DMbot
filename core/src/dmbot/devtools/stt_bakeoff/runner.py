"""Running the bake-off: phases, resumable results, and re-listen.

Results go to `<out>/results.jsonl`, one JSON object per request. A run can be stopped
and started again: finished requests are skipped, failed ones are tried again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dmbot.devtools.stt_bakeoff import data
from dmbot.devtools.stt_bakeoff.audio import read_wav
from dmbot.devtools.stt_bakeoff.data import LINE_BY_N, NAME_BY_CANONICAL, NAMES, VocabEntry
from dmbot.devtools.stt_bakeoff.providers import (
    Deepgram,
    Provider,
    Result,
    Speechmatics,
    Whisper,
)
from dmbot.devtools.stt_bakeoff.score import align, heard_as, misheard_forms, score

log = logging.getLogger(__name__)

MAIN, CLEAN, LEARNED, RELISTEN, TIMING = "main", "clean", "learned", "relisten", "timing"
PHASES = (MAIN, CLEAN, LEARNED, RELISTEN, TIMING)

# Which word lists each kind of service is tested with (the learned list is its own phase).
SM_LISTS = (data.NONE, data.SCENE, data.SCENE_SL, data.BIG)
DG_LISTS = (data.NONE, data.SCENE, data.BIG)
WHISPER_LISTS = (data.NONE, data.SCENE)
SCENE_SIZE = len(data.word_list(data.SCENE))

SETUPS: dict[str, tuple[Callable[[], Provider], tuple[str, ...]]] = {
    "sm-standard": (lambda: Speechmatics("standard"), SM_LISTS),
    "sm-enhanced": (lambda: Speechmatics("enhanced"), SM_LISTS),
    # Optional: Speechmatics' newer, cheaper model, if the account offers it in real time.
    "sm-melia": (lambda: Speechmatics("melia-1"), SM_LISTS),
    "dg-nova3": (lambda: Deepgram("nova-3", min_terms=SCENE_SIZE), DG_LISTS),
    "whisper": (Whisper, WHISPER_LISTS),
}
DEFAULT_SETUPS = ("sm-standard", "sm-enhanced", "dg-nova3", "whisper")
LEARN_FROM = "sm-enhanced"  # whose Scene mistakes become the learned sounds-like hints
CLEAN_SETUPS = ("sm-enhanced", "dg-nova3")
TIMING_SETUPS = ("sm-enhanced", "dg-nova3")
RELISTEN_CANDIDATES = 5
# Re-listen controls: trap lines re-sent with a shortlist of names that sound like a
# word in them. Any name written then is a false name forced by the shortlist.
RELISTEN_CONTROLS = {50: "bell or", 53: "quill on", 56: "kale"}


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
    target: str = ""  # re-listen: the name being re-checked ("" for a control)
    total_s: float = 0.0
    close_s: float = 0.0
    tail_s: float = 0.0  # padding after the speech in this clip
    shortlist_hit: bool | None = None  # re-listen: was the right name in the shortlist?

    @property
    def speech_to_final_s(self) -> float:
        """From the end of speech (not the end of the clip) to the final text."""
        return self.final_s + self.tail_s


def key_of(*parts: object) -> str:
    return "|".join(str(p) for p in parts)


def vocab_hash(vocab: list[VocabEntry]) -> str:
    raw = json.dumps([[v.content, list(v.sounds_like)] for v in vocab])
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


class Store:
    def __init__(self, out: Path) -> None:
        self.path = out / "results.jsonl"
        self.done: set[str] = {r.key for r in load(self.path) if not r.error}

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
    tail_s: float


def clips(out: Path, variant: str, limit: int | None = None) -> Iterator[Clip]:
    """Clips from the manifest; `limit` = only the first N lines of each reader."""
    manifest = json.loads((out / "manifest.json").read_text())
    for reader, info in sorted(manifest["readers"].items()):
        use = variant if variant in info["variants"] else "clean"
        ordered = sorted(info["lines"].items(), key=lambda kv: int(kv[0]))
        for line_s, files in ordered[:limit]:
            yield Clip(reader, int(line_s), use, out / files[use], float(files.get("tail_s", 0)))


class LearnedSourceMissing(RuntimeError):
    pass


class Runner:
    def __init__(self, out: Path, concurrency: int = 2) -> None:
        self.out = out
        self.store = Store(out)
        self.sem = asyncio.Semaphore(concurrency)
        self._providers: dict[str, Provider] = {}

    def provider(self, setup: str) -> Provider:
        if setup not in self._providers:
            self._providers[setup] = SETUPS[setup][0]()
        return self._providers[setup]

    async def _request(
        self, setup: str, clip: Clip, vocab: list[VocabEntry], realtime: bool
    ) -> Result:
        return await self.provider(setup).transcribe(read_wav(clip.path), vocab, realtime=realtime)

    def _save(
        self,
        key: str,
        phase: str,
        setup: str,
        wordlist: str,
        clip: Clip,
        vocab: list[VocabEntry],
        res: Result,
        **extra: Any,
    ) -> None:
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
                extra.pop("repeat", 0),
                res.text,
                res.words,
                res.connect_s,
                res.final_s,
                res.audio_s,
                res.error,
                res.note,
                len(vocab),
                total_s=res.total_s,
                close_s=res.close_s,
                tail_s=clip.tail_s,
                **extra,
            )
        )

    async def _one(
        self,
        key: str,
        phase: str,
        setup: str,
        wordlist: str,
        clip: Clip,
        vocab: list[VocabEntry],
        *,
        realtime: bool = True,
        **extra: Any,
    ) -> None:
        if key in self.store.done:
            return
        async with self.sem:
            res = await self._request(setup, clip, vocab, realtime)
        self._save(key, phase, setup, wordlist, clip, vocab, res, **extra)

    async def _gather(self, jobs: list[Coroutine[Any, Any, None]]) -> None:
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
                for clip in clips(self.out, "discord", limit):
                    k = key_of(MAIN, setup, wl, clip.reader, clip.variant, clip.line)
                    jobs.append(self._one(k, MAIN, setup, wl, clip, vocab))
        await self._gather(jobs)

    async def clean(self, setups: list[str], limit: int | None = None) -> None:
        jobs = []
        for setup in [s for s in CLEAN_SETUPS if s in setups]:
            vocab = data.word_list(data.SCENE)
            for clip in clips(self.out, "clean", limit):
                k = key_of(CLEAN, setup, data.SCENE, clip.reader, clip.variant, clip.line)
                jobs.append(self._one(k, CLEAN, setup, data.SCENE, clip, vocab))
        await self._gather(jobs)

    def learned_hints(self, reader: str) -> dict[str, set[str]]:
        """Sounds-like forms learned from the *other* readers' mistakes (so a reader's
        own clips are never used to tune the list they're tested with).

        Refuses to guess: every other reader's Scene results must be complete.
        """
        expected = {(c.reader, c.line) for c in clips(self.out, "discord") if c.reader != reader}
        have: dict[tuple[str, int], Record] = {}
        for rec in load(self.store.path):
            source = (rec.phase, rec.setup, rec.wordlist) == (MAIN, LEARN_FROM, data.SCENE)
            if source and rec.reader != reader and not rec.error:
                have[(rec.reader, rec.line)] = rec
        missing = expected - have.keys()
        if not expected or missing:
            raise LearnedSourceMissing(
                f"The learned list for {reader} needs {LEARN_FROM} Scene results for every "
                f"other reader's clip ({len(missing)} missing). Run the main phase first."
            )
        hints: dict[str, set[str]] = {}
        for rec in have.values():
            for name, heard in misheard_forms(LINE_BY_N[rec.line], rec.words).items():
                hints.setdefault(name, set()).add(heard)
        return hints

    async def learned(self, setups: list[str], limit: int | None = None) -> None:
        jobs = []
        by_reader: dict[str, list[VocabEntry]] = {}
        for setup in [s for s in setups if s.startswith("sm-")]:
            for clip in clips(self.out, "discord", limit):
                if clip.reader not in by_reader:
                    hints = self.learned_hints(clip.reader)
                    by_reader[clip.reader] = data.word_list(data.LEARNED, hints)
                    log.info(
                        "Learned hints for %s: %s",
                        clip.reader,
                        {n: len(h) for n, h in sorted(hints.items())},
                    )
                vocab = by_reader[clip.reader]
                k = key_of(LEARNED, setup, vocab_hash(vocab), clip.reader, clip.variant, clip.line)
                jobs.append(self._one(k, LEARNED, setup, data.LEARNED, clip, vocab))
        await self._gather(jobs)

    async def relisten(self, setups: list[str]) -> None:
        """Re-send clips whose names were missed with the Scene list, with a shortlist
        built from what the service wrote (DMbot won't know the right name live), sent
        all at once since the audio already exists. Plus trap-line controls."""
        jobs = []
        paths = {(c.reader, c.line): c for c in clips(self.out, "discord")}
        for rec in load(self.store.path):
            if (rec.phase, rec.wordlist) != (MAIN, data.SCENE) or rec.error:
                continue
            if rec.setup not in setups or rec.setup == "whisper":
                continue
            clip = paths.get((rec.reader, rec.line))
            if clip is None:
                continue
            sm = rec.setup.startswith("sm-")
            for missed in score(rec.line, rec.words).missed:
                heard = heard_as(LINE_BY_N[rec.line], rec.words, missed)
                vocab = shortlist(heard, with_sounds_like=sm)
                k = key_of(RELISTEN, rec.setup, missed, rec.reader, clip.variant, rec.line)
                jobs.append(
                    self._one(
                        k,
                        RELISTEN,
                        rec.setup,
                        "shortlist",
                        clip,
                        vocab,
                        realtime=False,
                        target=missed,
                        shortlist_hit=missed in [v.content for v in vocab],
                    )
                )
        for setup in [s for s in setups if s != "whisper"]:
            for (reader, line), clip in sorted(paths.items()):
                if line in RELISTEN_CONTROLS:
                    vocab = shortlist(RELISTEN_CONTROLS[line], setup.startswith("sm-"))
                    k = key_of(RELISTEN, setup, "control", reader, clip.variant, line)
                    jobs.append(
                        self._one(k, RELISTEN, setup, "control", clip, vocab, realtime=False)
                    )
        await self._gather(jobs)

    async def timing(self, setups: list[str], lines: int = 20, repeats: int = 3) -> None:
        """Speed, one request at a time so nothing competes: a discarded warm-up, then
        repeats with the Scene list (dictionary cached), then 'cold' runs where a one-off
        extra word forces a new dictionary (Speechmatics only: Deepgram caches nothing)."""
        base = data.word_list(data.SCENE)
        subset = [c for c in clips(self.out, "discord") if c.line <= lines]
        for setup in [s for s in TIMING_SETUPS if s in setups]:
            if subset:
                await self._request(setup, subset[0], base, True)  # warm-up, not recorded
            for rep in range(repeats):
                for clip in subset:
                    k = key_of(TIMING, setup, "warm", rep, clip.reader, clip.line)
                    if k not in self.store.done:
                        res = await self._request(setup, clip, base, True)
                        self._save(k, TIMING, setup, data.SCENE, clip, base, res, repeat=rep)
            if not setup.startswith("sm-"):
                continue
            for rep in range(repeats):
                for clip in subset:
                    k = key_of(TIMING, setup, "cold", rep, clip.reader, clip.line)
                    if k not in self.store.done:
                        vocab = [*base, VocabEntry("Zx" + uuid.uuid4().hex[:8])]
                        res = await self._request(setup, clip, vocab, True)
                        self._save(
                            k, TIMING, setup, data.SCENE, clip, vocab, res, repeat=rep, cold=True
                        )


def shortlist(heard: str, with_sounds_like: bool) -> list[VocabEntry]:
    """Up to 5 names closest in spelling to what was heard (spaces ignored)."""
    target = heard.replace(" ", "").casefold()

    def distance(name: str) -> tuple[int, str]:
        dist, _ = align(list(target), list(name.replace(" ", "").replace("'", "").casefold()))
        return dist, name

    names = sorted((n.canonical for n in NAMES if not n.is_word), key=distance)
    return [
        VocabEntry(c, NAME_BY_CANONICAL[c].sounds_like if with_sounds_like else ())
        for c in names[:RELISTEN_CANDIDATES]
    ]
