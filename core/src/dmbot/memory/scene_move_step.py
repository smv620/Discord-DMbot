"""Find a session's scene moves (#4173 slice 4; the owner's change and Supervisor's cascade,
2026-11-13): the DM's lines whose stored Voyage numbers look like a move (`scene_moves.likely`),
each with the line before and after, go to the AI in one call (more only for a long session), and
the moves it names come back by line. Nothing new about the lines is sent to Voyage: their numbers
are the ones PlotBot's search already keeps (`memory_vectors`); only the fixed, invented example
sentences are turned into numbers, once per model per run of the bot.

- **No numbers, no filter.** With meaning search off, no Voyage key, or a DM line not turned into
  numbers yet, that line is asked about anyway (never quietly skipped) and counted as a fallback.
- **Consent.** Only the lines the caller passes (people who agree, the DM included only if the DM
  agrees), and again after each reply: a move on a line of someone who stopped is dropped.
- **AI output rules** (CLAUDE.md): the background floor for `max_tokens`; a cut reply keeps what
  came whole before the cut and the rest of its lines are asked again in smaller parts; unreadable:
  counted, never read as "nothing found"; no length checks.
- **Counts only** in the log and the result: never a line's words.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import scene_moves as sm

log = logging.getLogger(__name__)
CALL_TIMEOUT_S = 300
MAX_TOKENS = 16_000  # (the background floor: the service charges for what is written)


class _Client(Protocol):
    """What `find` needs of the AI client (the extractor's, as `scene_step`)."""

    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...


class _Embedder(Protocol):
    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class DmLine:
    """One line of the session the walk reads: its number in the session, who said it, its words,
    and its stored numbers (None when it has none)."""

    number: int
    user_id: int
    text: str
    vector: Sequence[float] | None


@dataclass(slots=True)
class Found:
    moves: dict[int, list[sm.Move]] = field(default_factory=dict)  # line number -> its moves
    dm_lines: int = 0
    asked: int = 0  # DM lines sent to the AI
    fallback: int = 0  # of those, sent only because they had no numbers
    calls: int = 0
    unreadable: int = 0
    stopped_early: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    out_of_funds: bool = False


_EXAMPLES: dict[str, tuple[list[list[float]], list[list[float]]]] = {}  # model -> (moves, misses)


async def example_vectors(
    voyage: _Embedder, model: str
) -> tuple[list[list[float]], list[list[float]]]:
    """The fixed examples' numbers for this model, made once per run of the bot (a few dozen
    invented sentences, no one's words)."""
    if model not in _EXAMPLES:
        moves = [t for kind in sm.EXAMPLES.values() for t in kind]
        got = await voyage.embed([*moves, *sm.NEAR_MISSES])
        _EXAMPLES[model] = (got[: len(moves)], got[len(moves) :])
    return _EXAMPLES[model]


def to_ask(
    lines: Sequence[DmLine],
    dm_ids: Collection[int],
    examples: tuple[list[list[float]], list[list[float]]] | None,
) -> tuple[list[int], int]:
    """The DM lines to ask about, and how many of them only because they had no numbers. Pure."""
    asked: list[int] = []
    fallback = 0
    for line in lines:
        if line.user_id not in dm_ids:
            continue
        if examples is None or line.vector is None:
            asked.append(line.number)
            fallback += 1
        elif sm.likely(line.vector, examples[0], examples[1]):
            asked.append(line.number)
    return asked, fallback


async def find(
    client: _Client,
    lines: Sequence[DmLine],
    dm_ids: Collection[int],
    examples: tuple[list[list[float]], list[list[float]]] | None,
    *,
    count_call: Callable[[], Awaitable[None]],
    allowed: int,
    agreed: Callable[[], Awaitable[Collection[int]]],
) -> Found:
    """Ask the AI about the likely DM lines (with context), at most `allowed` calls."""
    out = Found(dm_lines=sum(1 for line in lines if line.user_id in dm_ids))
    asked, out.fallback = to_ask(lines, dm_ids, examples)
    out.asked = len(asked)
    numbers = [line.number for line in lines]
    by_number = {line.number: line for line in lines}
    parts = [asked[i : i + sm.LINES_PER_CALL] for i in range(0, len(asked), sm.LINES_PER_CALL)]
    while parts and out.calls < allowed and not out.out_of_funds:
        part = parts.pop(0)
        sent = sm.with_context(set(part), numbers)
        text = sm.items_text([(n, by_number[n].text, n in part) for n in sent])
        await count_call()
        out.calls += 1
        try:
            reply = await asyncio.wait_for(
                client.complete(sm.SYSTEM, text, max_tokens=MAX_TOKENS), CALL_TIMEOUT_S
            )
        except AIOutOfFunds:
            out.out_of_funds = True
            break
        except Exception as exc:  # (the type only: a provider error can quote the request)
            log.warning("Scene moves: a call failed (%s)", type(exc).__name__)
            out.unreadable += 1
            continue
        out.input_tokens += reply.input_tokens
        out.output_tokens += reply.output_tokens
        parsed = sm.parse_moves(reply.text, set(part), cut=reply.cut)
        if parsed.unreadable:
            out.unreadable += 1
            continue
        still = set(await agreed())  # (consent again, after the reply)
        for m in parsed.moves:
            if by_number[m.line].user_id in still:
                out.moves.setdefault(m.line, []).append(m)
        if parsed.stopped_early:  # (the rest of this part again, in halves)
            out.stopped_early += 1
            done = max((m.line for m in parsed.moves), default=0)
            rest = [n for n in part if n > done]
            if len(rest) > 1:
                half = len(rest) // 2
                parts[:0] = [rest[:half], rest[half:]]
            elif rest:
                parts.insert(0, rest)
    return out


def moves_of(found: Found) -> Mapping[int, tuple[sm.Move, ...]]:
    return {n: tuple(ms) for n, ms in found.moves.items()}
