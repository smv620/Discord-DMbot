"""Running PlotBot's name pass on one stored session (#2628): the part the bot and dev1's
`devtools.name_pass` share, so the tool shows exactly what a live session would do.

It reads the session's cleaned lines, finds the candidates (`name_pass.survey`), asks PlotBot once
(`ask` is given: the bot's own client, or the tool's), believes only sure answers that name an
offered list name, and writes them into the cleaned text, never the raw (#933), one Undo number for
each list name. With `dry_run` nothing is written: the same fixes are counted on a copy.

What it returns is counts, the list names, and the spellings as heard in `Report`: the spellings
are words from a transcript, for the campaign's DM and dev1's terminal only; this module logs
none of them.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Collection
from typing import TYPE_CHECKING, Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import name_key
from dmbot.transcript import name_pass as np
from dmbot.transcript.respell import contains, respell
from dmbot.transcript.topics import GAME

if TYPE_CHECKING:
    from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)

# (instructions, question) -> PlotBot's reply: its text, or an object with `.text` and `.cut` (the
# model stopped at its limit, #2649), so a cut answer is told apart from a plain "unsure".
Ask = Callable[[str, str], Awaitable[Any]]


async def pass_lines(
    store: TranscriptStore,
    guild_id: int,
    session_id: str,
    agreed: Collection[int] | None,
    game_only: bool = False,
) -> list[str]:
    """The cleaned lines the pass reads: speech from people who still agree, not the DM sidebar,
    not typed lines and, for an imported session, only what the filter kept as game talk. One
    place, so the live pass and dev1's tool read the same lines (#2649)."""
    return [
        x.text
        for x in await store.lines(guild_id, session_id)
        if not x.sidebar
        and not x.typed
        and (agreed is None or x.user_id in agreed)
        and (not game_only or x.topic == GAME)
    ]


async def run(
    store: TranscriptStore,
    lookup: CampaignLookup,
    ask: Ask,
    guild_id: int,
    campaign_id: str,
    session_id: str,
    *,
    people: Collection[str] = (),
    known: Collection[str] = (),
    agreed: Collection[int] | None = None,
    now: int,
    forget_before: int,
    dry_run: bool = False,
    game_only: bool = False,
) -> np.Report:
    texts = await pass_lines(store, guild_id, session_id, agreed, game_only)
    found = np.survey(texts, lookup, people)
    if not found.items:
        return np.Report(guarded=found.guarded)
    # The words PlotBot was not sure of, and the ones it says are new names, are offered to the DM
    # as name questions: what the campaign knows already (`known`, its keys) and the people at the
    # table (`people`) are not, by the same rule as the name scan. Counted here, so the live pass
    # and dev1's tool print the same numbers (#2655).
    skip = set(known) | set(people)
    reply = await ask(np.SYSTEM, np.question(found.items))
    cut = bool(getattr(reply, "cut", False))
    verdict = np.decide(reply if isinstance(reply, str) else str(reply.text), found.items)
    hidden = [n.text for n in lookup.names if n.secret or n.entity_id in lookup.hidden]
    # One list name may be meant by several heard spellings: they are one fix with one Undo.
    by_name: dict[str, tuple[str, Counter[str]]] = {}
    for decision in verdict.sure:
        batch = np.batch_of(session_id, decision.entity_id)
        forms: Counter[str] = Counter()
        for old in (decision.heard, *decision.also):
            longer = [n.text for n in lookup.names if n.text != old and contains(n.text, old)]
            if dry_run:
                for text in texts:
                    found_here = respell(text, old, decision.name, longer, hidden)
                    if found_here is not None:
                        forms.update(found_here.was)
            else:
                done = await store.respell_names(
                    guild_id, campaign_id, batch, old, decision.name, longer, hidden,
                    now=now, forget_before=forget_before, session_id=session_id,
                    speakers=agreed, game_only=game_only,
                )  # fmt: skip
                forms.update(dict(done.forms))
        if forms:
            by_name.setdefault(decision.entity_id, (decision.name, Counter()))[1].update(forms)
    fixed = [
        np.Fixed(name, np.group_forms(forms.items()), np.batch_of(session_id, entity_id))
        for entity_id, (name, forms) in by_name.items()
    ]
    fixed.sort(key=lambda f: (-f.times, f.name))
    log.info("Name pass: %d asked, %d sure", len(found.items), len(verdict.sure))  # counts only
    if cut:  # every word the cut answer did not reach is "unsure": safe, and now it shows (#2596)
        log.warning("Name pass: the answer was cut off (%d words asked)", len(found.items))
    return np.Report(
        tuple(fixed),
        tuple(c for c in verdict.unsure if name_key(c.heard) not in skip),
        tuple(c for c in verdict.new if name_key(c.heard) not in skip),
        found.guarded,
        cut,
    )
