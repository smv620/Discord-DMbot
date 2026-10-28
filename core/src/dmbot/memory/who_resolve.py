"""Settling the pronouns a lore read left open (#2790, the wiring slice).

`background.read_connections` hands over a connection whose quote holds one of its two names and
one pronoun ("she") when the sentence before did not settle who the pronoun means. This module asks
`who_ask.ask` about each, one at a time, and turns the answer into what the read keeps: the
connection with its quote widened to show both names, a "Which one?" question for the DM, or the
relationship with an unnamed person. It never decides alone on a doubt (an accepted name the AI's
own reading disagrees with is a question), never invents a name, and drops nothing without a count.

Each lookup costs several AI calls, so a read has a cap (`WHO_LOOKUPS_PER_READ`) and a time budget;
past either, the rest are kept with an unnamed person, counted. Only counts are logged or shown:
never a name or a word of the text. Pure apart from the AI client the caller passes in.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from dmbot.memory import background as bg
from dmbot.memory import who_ask
from dmbot.memory.models import name_key
from dmbot.memory.who_is_it import NOT_LISTED, Verdict

log = logging.getLogger(__name__)

WHO_LOOKUPS_PER_READ = 30  # each is 6 AI calls; the rest of a read's pronouns are kept unnamed
WHO_TIME_LIMIT_S = 600  # a read's lookups stop here; the rest are kept unnamed
SOMEONE = "someone"  # the word for an unnamed person when the story gives no relationship word


@dataclass(frozen=True, slots=True)
class UnnamedLink:
    """A relationship kept with a person the read could not name: `fixed` is the end that is a
    name (the connection's first end when `fixed_is_a`), `what` the story's word for the other
    ("sister"), `anchored` whether that word hangs off `fixed` ("Ken's sister")."""

    fixed: str
    fixed_is_a: bool
    how: str
    quote: str
    words: str
    what: str
    anchored: bool
    role: str
    secret: bool = False  # set by the caller from the named end, as `keep_connections` does
    decided: str = ""  # how it was settled, in fixed words (#2790)


@dataclass
class Settled:
    connections: list[bg.Connection] = field(default_factory=list)  # to `keep_connections`
    picks: list[bg.Pick] = field(default_factory=list)  # "Which one?" for the DM
    unnamed: list[UnnamedLink] = field(default_factory=list)
    # Counts only (never a name): accepted, asked, unnamed, over_cap, over_time, ai_failed, unsure.
    counts: Counter[str] = field(default_factory=Counter)
    unresolved: int = 0  # lines that could not be kept in any form: counted as `not_in_text`


def decided_words(what: str, score: float | None = None) -> str:
    """How a pronoun was settled, in fixed words and a rounded score only (never a name or any
    text), kept with the waiting connection so the DM sees DMbot's check beside the quote."""
    return f"who-is-it: {what}" + (f" {round(score)}%" if score is not None else "")


def window_of(passage: str) -> str:
    """The last `QUOTE_WORDS` words of the passage, as written: the new quote, which ends where
    the AI's quote ended and so holds the pronoun and the end that was named."""
    return " ".join(passage.split()[-bg.QUOTE_WORDS :])


def _fixed(u: bg.Unsettled) -> str:
    return u.made.b if u.missing_is_a else u.made.a


def _missing(u: bg.Unsettled) -> str:
    return u.made.a if u.missing_is_a else u.made.b


def _unnamed(u: bg.Unsettled, quote: str, decided: str) -> UnnamedLink:
    kin = u.made.how == "kin_of" and bool(u.made.words)
    return UnnamedLink(
        fixed=_fixed(u),
        fixed_is_a=not u.missing_is_a,
        how=u.made.how,
        quote=quote,
        words=u.made.words,
        what=u.made.words if kin else SOMEONE,
        anchored=kin,
        role=u.made.words or u.made.how,
        decided=decided,
    )


def _pick(u: bg.Unsettled, quote: str, names: Sequence[str], decided: str) -> bg.Pick | None:
    """The question for the DM: two or three names, the best first, never the named end itself."""
    fixed = name_key(_fixed(u))
    chosen: list[str] = []
    for n in names:
        if n == NOT_LISTED or name_key(n) == fixed or name_key(n) in map(name_key, chosen):
            continue
        chosen.append(n)
    chosen = chosen[: bg.MAX_PICK_CHOICES]
    if not chosen:  # (one name is enough: "🙋 Someone else" is always the other choice)
        return None
    return bg.Pick(
        _fixed(u), not u.missing_is_a, u.phrase, tuple(chosen), u.made.how, quote, False,
        u.made.words, decided,
    )  # fmt: skip


def _use(u: bg.Unsettled, asked: who_ask.Asked | None, out: Settled) -> None:
    """Turn one answer (or the lack of one) into what the read keeps."""
    quote = window_of(u.passage)
    if asked is None:  # the AI could not answer: the relationship is kept, nothing guessed
        out.counts["ai_failed"] += 1
        out.unnamed.append(_unnamed(u, quote, decided_words("not checked")))
        return
    decision = who_ask.decide_asked(asked)
    if decision.verdict is Verdict.UNNAMED:
        out.counts["unnamed"] += 1
        out.unnamed.append(_unnamed(u, quote, decided_words("nobody listed", decision.top)))
        return
    top = decision.name or ""
    ranked = [c.name for c in asked.candidates]
    agrees = name_key(top) == name_key(_missing(u))
    spelled = (u.made.variant_a if u.missing_is_a else u.made.variant_b) or _missing(u)
    shown = bg.quote_holds(quote, spelled) and bg.quote_holds(
        quote, (u.made.variant_b if u.missing_is_a else u.made.variant_a) or _fixed(u)
    )
    if decision.verdict is Verdict.ACCEPT and agrees and shown:
        out.counts["accepted"] += 1
        out.connections.append(
            replace(u.made, quote=quote, decided=decided_words("accepted", decision.top))
        )
        return
    # A doubt (the rule asked, or the AI's own reading and the check disagree, or the named end is
    # too far back to show in the quote): the DM decides between the names.
    order = [top, _missing(u), *ranked]
    pick = _pick(u, quote, order, decided_words("asked", decision.top))
    if pick is None:  # (no name at all to offer: the relationship is kept with an unnamed person)
        out.counts["unsure"] += 1
        out.unnamed.append(_unnamed(u, quote, decided_words("unsure", decision.top)))
        return
    out.counts["asked"] += 1
    out.picks.append(pick)


async def settle(
    unsettled: Sequence[bg.Unsettled],
    client: who_ask.Client,
    names: Sequence[str],
    *,
    cap: int = WHO_LOOKUPS_PER_READ,
    until: float | None = None,
    time_limit_s: float = WHO_TIME_LIMIT_S,
    progress: Callable[[int, int], None] | None = None,
) -> Settled:
    """Every open pronoun settled or kept, shortest passage first (the passage ends where the
    quote does, so that is the order of the text within a slice). `names` are the campaign's names
    the AI may pick from (only those in the passage are sent). `progress(done, total)` is told
    after each lookup, counts only, so a long run never looks stalled (#2564).

    Time: `until` is a `time.monotonic()` moment the caller must be done by (a read has Discord's
    15 minutes in all, so the lookups cannot have a clock of their own); `time_limit_s` is a
    shorter budget for the lookups themselves. A lookup in flight is cut at the earlier of the
    two. Whatever is not looked up is kept with an unnamed person, counted, never dropped."""
    out = Settled()
    ordered = sorted(unsettled, key=lambda u: len(u.passage))
    total = min(len(ordered), cap)
    started = time.monotonic()
    stop = started + time_limit_s if until is None else min(started + time_limit_s, until)
    if progress is not None:
        progress(0, total)
    for index, u in enumerate(ordered):
        if index >= cap:
            out.counts["over_cap"] += 1
            out.unnamed.append(
                _unnamed(u, window_of(u.passage), decided_words("not checked, over the limit"))
            )
            continue
        left = stop - time.monotonic()
        if left <= 0:
            out.counts["over_time"] += 1
            out.unnamed.append(
                _unnamed(u, window_of(u.passage), decided_words("not checked, out of time"))
            )
            continue
        offered = bg.names_in(u.passage[-who_ask.MAX_PASSAGE_CHARS :], names)
        try:
            asked = await asyncio.wait_for(
                who_ask.ask(client, u.passage, u.phrase, offered), timeout=left
            )
        except TimeoutError:  # (cut at the deadline: kept unnamed like a failed call)
            out.counts["over_time"] += 1
            out.unnamed.append(
                _unnamed(u, window_of(u.passage), decided_words("not checked, out of time"))
            )
        else:
            _use(u, asked, out)
        if progress is not None:
            progress(min(index + 1, total), total)
    if progress is not None:
        progress(total, total)
    log.info(
        "Who-is-it: %d open pronoun(s), %d accepted, %d asked, %d unnamed, %d unsure, "
        "%d failed, %d over the cap, %d over the time",
        len(ordered),
        out.counts["accepted"],
        out.counts["asked"],
        out.counts["unnamed"],
        out.counts["unsure"],
        out.counts["ai_failed"],
        out.counts["over_cap"],
        out.counts["over_time"],
    )
    return out
