"""Measure the recap writer on the invented golden set (#1382).

    python -m dmbot.devtools.recap_measure                       # free: checks the set
    python -m dmbot.devtools.recap_measure --writer anthropic    # paid: runs the real stages

Four numbers for each session: beat recall (how many of the beats a good sheet holds were found),
invented beats (must be 0: beats the checks had to cut because no real line said them), quote
leaks (must be 0: prose that copies the table) and secret leaks (must be 0). Paid calls happen
only with `--writer anthropic`, never in CI (CI runs the set's format check and the scoring on
canned answers). The key comes from ANTHROPIC_API_KEY in the environment and is never printed.
The paid run is dev1's, with a key; the prices to put in docs/costs.md come from it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.ai import AIError, AIModels, AIModelTier, AnthropicClient
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.common import add_fast_model_arg, with_fast_model
from dmbot.devtools.recap_measure.golden import GoldenError, Session, load_golden
from dmbot.memory.extractor import readable
from dmbot.recaps import beats as bt
from dmbot.recaps import draft as rd
from dmbot.recaps.pipeline import Client
from dmbot.recaps.text import names_a_secret, narration_of, words


@dataclass(slots=True)
class Score:
    sessions: int = 0
    reference: int = 0
    found: int = 0
    invented: int = 0
    quote_leaks: int = 0
    secret_leaks: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.reference if self.reference else 0.0


def beat_hits(session: Session, kept: Sequence[bt.Beat]) -> int:
    """How many reference beats some produced beat covers: at least two thirds of its key words
    (stems of five letters), in the beat's words or its names."""
    hits = 0
    for keys in session.reference:
        for beat in kept:
            have = {w[:5] for w in words(beat.what + " " + " ".join(beat.who))}
            if sum(k[:5] in have for k in keys) * 3 >= len(keys) * 2:
                hits += 1
                break
    return hits


def score_session(
    session: Session, raw_beats: Sequence[dict[str, object]], prose: rd.Prose | None, title: str
) -> Score:
    """Score one session's beats and prose against the reference. Pure: no AI call here."""
    said = readable(session.lines)
    kept, _ = bt.check(
        raw_beats,
        lines={s.number: s.text for s in said},
        speakers={s.number: s.user_id for s in said},
        allowed_names=session.names,
        secret_names=session.secret,
    )
    out = Score(sessions=1, reference=len(session.reference), found=beat_hits(session, kept))
    out.invented = len(raw_beats) - len(kept)
    if prose is not None:
        transcript = "\n".join(s.text for s in said)
        narration = narration_of(prose.summary)
        out.quote_leaks = int(rd.copies_the_table(narration, transcript) or '"' in narration)
        out.secret_leaks = int(
            names_a_secret(" ".join([title, prose.summary, *prose.music]), session.secret, [])
        )
    return out


def add(total: Score, one: Score) -> None:
    for name in ("sessions", "reference", "found", "invented", "quote_leaks", "secret_leaks"):
        setattr(total, name, getattr(total, name) + getattr(one, name))
    total.notes += one.notes


def report(total: Score) -> list[str]:
    return [
        f"sessions: {total.sessions}",
        f"beat recall: {total.found}/{total.reference} ({total.recall:.0%})",
        f"invented beats: {total.invented} (must be 0)",
        f"quote leaks: {total.quote_leaks} (must be 0)",
        f"secret leaks: {total.secret_leaks} (must be 0)",
        *total.notes,
    ]


async def run_one(fast: Client, session: Session) -> Score:
    """The real beat and prose stages for one session, one call each (no retries: a first-try
    measure of the writer, as it would be in the live run)."""
    said = readable(session.lines)
    rows = [(str(s.number), "DM" if s.user_id == 1 else "Player", s.text) for s in said]
    parts = bt.windows(rows, session.window_chars)
    found: list[dict[str, object]] = []
    for k, part in enumerate(parts, start=1):
        reply = await fast.complete(
            bt.system(k, len(parts)), bt.message(part, session.names), max_tokens=bt.MAX_TOKENS
        )
        found += bt.parse(reply.text) or []
    raw = bt.merge([found])
    kept, _ = bt.check(
        raw,
        lines={s.number: s.text for s in said},
        speakers={s.number: s.user_id for s in said},
        allowed_names=session.names,
        secret_names=session.secret,
    )
    prose: rd.Prose | None = None
    length = rd.length_for(rd.session_hours(readable(session.lines), None))
    if len(kept) >= bt.MIN_BEATS:
        written = await fast.complete(
            rd.prose_system(None, length),
            rd.prose_message(rd.pick_beats(kept, length.beats), "", session.names, None),
            max_tokens=length.max_tokens,
            schema=rd.prose_schema(),
        )
        prose = rd.parse_prose(written.text)
    return score_session(session, raw, prose, session.title)


async def main_async(args: argparse.Namespace) -> int:
    try:
        sessions = load_golden()
    except (OSError, GoldenError) as exc:
        print(f"recap_measure: {exc}", file=sys.stderr)
        return 2
    print(f"golden set: {len(sessions)} sessions, {sum(len(s.lines) for s in sessions)} lines")
    if args.writer == "none":
        print("No calls made: --writer anthropic measures (it costs money).")
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("recap_measure: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2
    try:
        models = with_fast_model(
            parse_ai_models(lambda name: os.environ.get(name, ""))[0], args.fast_model
        )
    except ConfigError as exc:
        print(f"recap_measure: {exc}", file=sys.stderr)
        return 2
    ai = AnthropicClient(key, AIModels.same(models.of(AIModelTier.FAST)))
    total = Score()
    try:
        for session in sessions:
            try:
                add(total, await run_one(ai.tier(AIModelTier.FAST), session))
            except AIError as exc:
                print(f"recap_measure: a call failed ({type(exc).__name__}); stopping")
                return 1
    finally:
        await ai.close()
    print("\n".join(report(total)))
    return 0 if not (total.invented or total.quote_leaks or total.secret_leaks) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recap_measure", description=__doc__)
    parser.add_argument("--writer", choices=("none", "anthropic"), default="none")
    add_fast_model_arg(parser)
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
