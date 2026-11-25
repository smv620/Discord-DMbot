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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace

from dmbot.memory import background as bg
from dmbot.memory import who_ask
from dmbot.memory import who_ranking as ranking
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


def checked_line(decided: str) -> str:
    """DMbot's check of a pronoun, in plain words, for a card ("" when there was none, #2790). The
    stored words are fixed (`who_resolve.decided_words`), so this maps them one by one. No score is
    shown: a DM at the table cannot act on a percentage, and "sure" would invite a rubber stamp."""
    how = decided.removeprefix("who-is-it: ").strip()
    if not how or how == decided:
        return ""
    if how.startswith("picked by the DM"):
        return "You picked this name.\n"
    if how.startswith("kept as someone else"):
        return "You chose someone else for this one.\n"
    if how.startswith("accepted"):
        return "DMbot matched this name from your text.\n"
    if how.startswith("asked"):
        return "DMbot wasn't sure who the text means. Please check.\n"
    if how.startswith(("nobody listed", "unsure")):
        return "DMbot couldn't tell who the text means. No name picked.\n"
    if how.startswith("not checked"):
        return "DMbot didn't check this one. Please check it yourself.\n"
    return ""


# A pronoun that can only mean a person: the ranking then leaves out places, things and groups.
# (From the reader's own table of pronoun forms, so the two lists cannot drift apart.)
PERSON_PRONOUNS = frozenset(w for w, one in bg._PERSON.items() if one in ("he", "she"))


def offer_for(
    u: bg.Unsettled, names: Sequence[str], pool: Sequence[ranking.Candidate] | None
) -> list[str]:
    """The names the AI is offered for one open pronoun (#2790 slice 2, Supervisor's ruling): at
    most `who_ranking.LIMIT`, scored by the same ranking as "Who is this?" (#3602), so there is one
    ranker. The pronoun's context stands in for a waiting name's: who the passage names is the
    scene, the connection's named end is who it belongs to (so "his sister" favours that person's
    family, and the named end itself is never offered), and a "he" or "she" is a character. The
    pronoun itself is no name to compare ("He Who Watches" is not likelier for a "he").

    A pronoun points back into the text, so the cut keeps the passage's people first: the end the
    reader named comes first when the passage holds it, then the passage's people by score and,
    among equals, nearest the pronoun, and only then people the passage doesn't name (family of
    the named end). Each name once, shown as the passage writes it when it does. Without a pool
    (the campaign could not be read) it offers the names the passage holds, as before."""
    tail = u.passage[-who_ask.MAX_PASSAGE_CHARS :]
    if pool is None:
        return bg.names_in(tail, names)
    allowed = {name_key(n) for n in names}
    scene: set[str] = set()
    owner: set[str] = set()
    fixed = name_key(_fixed(u))
    offerable: list[ranking.Candidate] = []
    for c in pool:
        # (the named end by any of its names, so "Ken" finds the entry "Ken Arlow")
        if fixed in {name_key(n) for n in c.names}:
            owner.add(c.id)
        # (only the names the read may settle a pronoun to: the same names as before this slice)
        kept = tuple(n for n in c.names if name_key(n) in allowed)
        if not kept:
            continue
        offerable.append(replace(c, names=kept))
        if bg.names_in(tail, kept):
            scene.add(c.id)
    phrase = u.phrase.strip().casefold()
    ask = ranking.Ask(
        "",
        "character" if phrase in PERSON_PRONOUNS else "",
        frozenset(scene),
        frozenset(),
        frozenset(owner),
    )
    missing = name_key(_missing(u))
    folded = tail.casefold()
    rows: list[tuple[tuple[int, int, float, int], str]] = []
    # (hidden from the players is no bar here: `names` already decides who may be offered)
    for r in ranking.rank(ask, offerable, len(offerable), include_hidden=True):
        written = bg.names_in(tail, r.candidate.names)
        shown = written[0] if written else r.candidate.names[0]
        here = r.candidate.id in scene
        near = folded.rfind(shown.casefold()) if here else -1
        is_missing = name_key(shown) == missing or missing in map(name_key, r.candidate.names)
        rows.append(((0 if is_missing and here else 1, 0 if here else 1, -r.score, -near), shown))
    out: list[str] = []
    for _, shown in sorted(rows, key=lambda row: row[0]):
        if name_key(shown) not in map(name_key, out):
            out.append(shown)
    return out[: ranking.LIMIT]


def pool_for(
    candidates: Sequence[ranking.Candidate],
    found: Sequence[tuple[str, str]],
    neighbours: Mapping[str, frozenset[str]] | None = None,
) -> list[ranking.Candidate]:
    """The campaign's entries (`who_step.candidates_from`) plus the names this read found that are
    no entry yet (name, kind), each its own candidate, so a name the upload introduced can still be
    the answer. A found name an entry already has is that entry.

    `neighbours` (the lookup's `confirmed_neighbours`, which hold no secret connection) replace each
    entry's ties: `candidates_from` leaves out ties to entries the players don't know yet, which
    in a lore read is nearly every entry, and the ties here only rank what the DM is asked; they are
    never shown as a reason. Who may be offered at all is `offer_for`'s `names`, which hold no
    secret name."""
    pool = [
        replace(c, ties=neighbours.get(c.id, frozenset())) if neighbours is not None else c
        for c in candidates
    ]
    have = {name_key(n) for c in pool for n in c.names}
    for name, kind in found:
        key = name_key(name)
        if key in have:
            continue
        have.add(key)
        pool.append(ranking.Candidate(f"read:{key}", kind or "character", (name,)))
    return pool


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


def _pick(
    u: bg.Unsettled,
    quote: str,
    names: Sequence[str],
    decided: str,
    players: Collection[str] = (),
) -> bg.Pick | None:
    """The question for the DM: two or three names, the best first, never the named end itself.
    A player character the passage names comes first (#3238): the family of the table's own
    characters is what a DM is most likely to be asked about, and the choice is cut at three."""
    fixed = name_key(_fixed(u))
    chosen: list[str] = []
    # only a player character the passage itself names goes first, so a character that is merely
    # a low-ranked guess never pushes out the check's own best answer
    named = {name_key(n) for n in bg.names_in(u.passage, names)}
    mine = {name_key(p) for p in players} & named
    names = sorted(names, key=lambda x: name_key(x) not in mine)
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


def _use(
    u: bg.Unsettled,
    asked: who_ask.Asked | None,
    out: Settled,
    players: Collection[str] = (),
    offered: Sequence[str] = (),
    ranking_used: bool = False,
) -> None:
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
    # (with the ranking, the card follows its order, the one the AI was offered (#2790 slice 2);
    # without it, the AI's own order as before)
    order = [top, _missing(u), *(offered if ranking_used else ranked)]
    pick = _pick(u, quote, order, decided_words("asked", decision.top), players)
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
    players: Collection[str] = (),
    pool: Sequence[ranking.Candidate] | None = None,
) -> Settled:
    """Every open pronoun settled or kept, shortest passage first (the passage ends where the
    quote does, so that is the order of the text within a slice). `names` are the campaign's names
    the AI may pick from (only those in the passage are sent). `progress(done, total)` is told
    after each lookup, counts only, so a long run never looks stalled (#2564).

    Time: `until` is a `time.monotonic()` moment the caller must be done by (a read has Discord's
    15 minutes in all, so the lookups cannot have a clock of their own); `time_limit_s` is a
    shorter budget for the lookups themselves. A lookup in flight is cut at the earlier of the
    two. Whatever is not looked up is kept with an unnamed person, counted, never dropped.

    `pool` (#2790 slice 2): the campaign's entries and this read's names, ranked per pronoun by
    `offer_for` so the AI is offered the best five; None offers the names the passage holds."""
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
        offered = offer_for(u, names, pool)
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
            _use(u, asked, out, players, offered, pool is not None)
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


PC_PICKS_PER_READ = (
    20  # open pronouns about the table's characters kept per read when the check is off
)


def pc_picks(
    unsettled: Sequence[bg.Unsettled], names: Sequence[str], players: Collection[str]
) -> Settled:
    """The who-is-it check is off (#3238): an open pronoun whose passage names a player character
    is not dropped. It waits for the DM as "Which one?" between the names in the passage, the
    player character first, with no AI and nothing guessed; every other open pronoun is left out
    and counted as before (`unresolved`). At most `PC_PICKS_PER_READ`, the rest counted
    (`over_cap`). Counts only, never a name."""
    out = Settled()
    mine = {name_key(p) for p in players}
    for u in sorted(unsettled, key=lambda x: len(x.passage)):
        offered = bg.names_in(u.passage[-who_ask.MAX_PASSAGE_CHARS :], names)
        fixed = name_key(_fixed(u))
        # (a player character that is only the end the sentence already names is no reason: the
        # pronoun is about someone else)
        if not any(name_key(n) in mine and name_key(n) != fixed for n in offered):
            out.unresolved += 1  # (as with the check off before: left out and counted)
            continue
        if out.counts["asked"] >= PC_PICKS_PER_READ:
            out.counts["over_cap"] += 1
            out.unresolved += 1
            continue
        pick = _pick(
            u, window_of(u.passage), [_missing(u), *offered], decided_words("not checked"), players
        )
        if pick is None:
            out.unresolved += 1
            continue
        out.counts["asked"] += 1
        out.picks.append(pick)
    return out
