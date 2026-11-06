"""Keeping a campaign's numbers up to date (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"): find the
campaign's text that has no numbers yet (or whose text changed), send it to Voyage AI in batches,
keep what comes back, and drop numbers whose text is gone.

**Consent is checked when the words are chosen and again after the call.** A line is sent only if
its speaker agrees to be recorded right now; when the numbers come back the speakers are read
again, and a person who stopped meanwhile has nothing kept (the same rule as every step that waits
on an AI). A line deleted meanwhile is never saved either (`VectorStore.save_lines`). A line whose
speaker no longer agrees but still exists loses its numbers at the next run (`drop_lines`).

**Nothing is sent unless `allowed()` says so** (the switch is on and the key is set; the caller
supplies it, and it is asked again before every call, so a switch turned off stops the run at the
next batch). Consent is likewise read again before EVERY batch is sent, so a person who stops
during an earlier batch is not sent in a later one.

**Long text is split, never cut.** A text longer than `PART_CHARS` goes as several parts at
paragraph, line, sentence or word breaks, each its own row; one that changes is made again whole.

**Every wait shows progress** (CLAUDE.md): `on_progress(stage, done, total)` after each batch,
counts and stage codes only, never a word of the text. The caller's `max_calls` is shared by the
whole run and `count_call` is told before each call, so the cost log and the caps see them. What
the limit leaves is made next time and counted as `left`, never taken for nothing to do. No credit
left stops the run (`stopped`); any other failure of a call stops it too and is counted, with a
code.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.ai import AIOutOfFunds
from dmbot.memory import vectors as vs
from dmbot.memory.voyage import BATCH, ProviderError

log = logging.getLogger(__name__)

PART_CHARS = 4000  # a text longer than this goes in parts (well inside what one text may hold)
STAGES = ("reading", "making", "keeping")
OUT_OF_FUNDS = "out_of_funds"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Piece:
    """One text to have numbers: `owner_id` carries a part number after `#` when a long text was
    split. `hash` is of the WHOLE text, so a changed text is made again in every part."""

    kind: str
    owner_id: str
    text: str
    hash: str
    speaker: int | None = None  # a line's speaker: needed for the consent check


class Source(Protocol):
    """Where the text comes from. Each is one kind of owner and reads one campaign."""

    kind: str

    async def pieces(self, guild_id: int, campaign_id: str) -> list[Piece]: ...


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


Consenting = Callable[[int], Awaitable[frozenset[int]]]
Allowed = Callable[[], Awaitable[bool]]
Progress = Callable[[str, int, int], Awaitable[None]]


def split_text(text: str, limit: int = PART_CHARS) -> list[str]:
    """`text` as parts of at most `limit` characters, cut at the largest break that fits: a blank
    line, a line, a sentence end, a space. Only a single word longer than `limit` is cut inside.
    Nothing is dropped: the parts joined with the breaks they were cut at are the text."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[: limit + 1]
        cut = 0
        for pattern in (r"\n\s*\n", r"\n", r"[.!?][\"')\]]*\s", r"\s"):
            ends = [m.end() for m in re.finditer(pattern, window)]
            ends = [e for e in ends if 0 < e <= limit]
            if ends:
                cut = ends[-1]
                break
        if cut == 0:
            cut = limit  # one word longer than the limit: the only time a cut lands inside
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def pieces_of(kind: str, owner_id: str, text: str, *, speaker: int | None = None) -> list[Piece]:
    """The pieces for one owner's text: one, or several when it is long."""
    whole = vs.text_hash(text)
    parts = split_text(text)
    if len(parts) <= 1:
        return [Piece(kind, owner_id, parts[0], whole, speaker)] if parts else []
    return [Piece(kind, f"{owner_id}#{n}", part, whole, speaker) for n, part in enumerate(parts, 1)]


@dataclass(slots=True)
class Embedded:
    """What a run did, as counts and codes (it goes in the log and the progress line)."""

    made: int = 0  # pieces that got numbers this time
    current: int = 0  # pieces that already had them
    dropped: int = 0  # numbers removed: text gone, speaker no longer agreeing, or another model
    left: int = 0  # pieces still to make (a call limit, or a stop)
    skipped: int = 0  # pieces of lines not sent or not kept because the speaker does not agree
    calls: int = 0
    failed: str = ""  # a code, or ""
    stopped: str = ""  # OUT_OF_FUNDS, or ""
    kinds: dict[str, int] = field(default_factory=dict)  # made, by kind

    def line(self) -> str:
        """Counts only, for the server log."""
        return (
            f"Embeddings: made={self.made} current={self.current} dropped={self.dropped} "
            f"left={self.left} skipped={self.skipped} calls={self.calls}"
            + (f" failed={self.failed}" if self.failed else "")
            + (f" stopped={self.stopped}" if self.stopped else "")
        )


def _may_send(piece: Piece, agreeing: frozenset[int]) -> bool:
    """A piece with a speaker (a line) only if that person agrees to be recorded now; a line with
    no speaker never. Text of the campaign's own (no speaker) is not a person's words."""
    if piece.speaker is not None:
        return piece.speaker in agreeing
    return piece.kind != vs.LINE


def owner_of(owner_id: str) -> str:
    """The owner behind a stored id: `12#2` (part 2 of a long text) is owner `12`. Searches
    de-duplicate by this."""
    return owner_id.split("#", 1)[0]


async def run(
    store: vs.VectorStore,
    embedder: Embedder,
    sources: Sequence[Source],
    consenting: Consenting,
    guild_id: int,
    campaign_id: str,
    model: str,
    *,
    allowed: Allowed,
    max_calls: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
    on_progress: Progress | None = None,
) -> Embedded:
    out = Embedded()

    async def progress(stage: str, done: int, total: int) -> None:
        if on_progress is None:
            return
        try:  # a display only: a failed write must not lose the work
            await on_progress(stage, done, total)
        except Exception as exc:
            log.warning("Embeddings: progress not saved (%s)", type(exc).__name__)

    await progress("reading", 0, 1)
    out.dropped += await store.drop_other_models(guild_id, campaign_id, model)
    agreeing = await consenting(guild_id)
    todo: list[Piece] = []
    for source in sources:
        pieces = await source.pieces(guild_id, campaign_id)
        agreed = [p for p in pieces if _may_send(p, agreeing)]
        out.skipped += len(pieces) - len(agreed)
        pieces = agreed
        kept = await store.kept(guild_id, campaign_id, model, kind=source.kind)
        wanted = {p.owner_id for p in pieces}
        stale = sorted(o for (k, o) in kept if k == source.kind and o not in wanted)
        if source.kind == vs.LINE:
            out.dropped += await store.drop_lines(guild_id, campaign_id, stale)
        else:
            out.dropped += await store.drop(guild_id, campaign_id, source.kind, stale)
        for piece in pieces:
            if kept.get((piece.kind, piece.owner_id)) == piece.hash:
                out.current += 1
            else:
                todo.append(piece)
    await progress("reading", 1, 1)
    total = len(todo)
    await progress("making", 0, total)
    for start in range(0, total, BATCH):
        if out.calls >= max_calls:
            out.left += total - start
            break
        if not await allowed():
            out.left = total - start
            break
        # consent read again right before sending: a person who stopped during an earlier batch
        # is not sent in this one
        agreeing = await consenting(guild_id)
        batch = [p for p in todo[start : start + BATCH] if _may_send(p, agreeing)]
        out.skipped += len(todo[start : start + BATCH]) - len(batch)
        if not batch:
            continue
        if count_call is not None:
            await count_call()
        out.calls += 1
        try:
            vectors = await embedder.embed([p.text for p in batch])
        except AIOutOfFunds:
            out.stopped, out.left = OUT_OF_FUNDS, total - start
            break
        except ProviderError as exc:
            log.warning("Embeddings: a call failed (%s)", exc.describe())
            out.failed, out.left = FAILED, total - start
            break
        await progress("keeping", start, total)
        # consent again: someone may have stopped while the text was away
        agreeing = await consenting(guild_id)
        items_by_kind: dict[str, list[vs.Item]] = {}
        for piece, vector in zip(batch, vectors, strict=True):
            if not _may_send(piece, agreeing):
                out.skipped += 1
                continue
            items_by_kind.setdefault(piece.kind, []).append(
                vs.Item(piece.kind, piece.owner_id, piece.hash, vector)
            )
        for kind, items in items_by_kind.items():
            kept_now = (
                await store.save_lines(guild_id, campaign_id, model, items)
                if kind == vs.LINE
                else await store.save(guild_id, campaign_id, model, items)
            )
            out.made += kept_now
            out.kinds[kind] = out.kinds.get(kind, 0) + kept_now
        await progress("making", min(start + BATCH, total), total)
    log.info(out.line())
    return out
