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
from typing import TYPE_CHECKING

from dmbot.memory.lookup import CampaignLookup
from dmbot.transcript import name_pass as np
from dmbot.transcript.respell import contains, respell

if TYPE_CHECKING:
    from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)

Ask = Callable[[str, str], Awaitable[str]]  # (instructions, question) -> PlotBot's reply text


async def run(
    store: TranscriptStore,
    lookup: CampaignLookup,
    ask: Ask,
    guild_id: int,
    campaign_id: str,
    session_id: str,
    *,
    people: Collection[str] = (),
    agreed: Collection[int] | None = None,
    now: int,
    forget_before: int,
    dry_run: bool = False,
) -> np.Report:
    stored = [
        x
        for x in await store.lines(guild_id, session_id)
        if not x.sidebar and not x.typed and (agreed is None or x.user_id in agreed)
    ]
    texts = [x.text for x in stored]
    found = np.survey(texts, lookup, people)
    if not found.items:
        return np.Report(guarded=found.guarded)
    reply = await ask(np.SYSTEM, np.question(found.items))
    verdict = np.decide(reply, found.items)
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
    return np.Report(
        tuple(fixed),
        tuple(verdict.unsure),
        tuple(verdict.new),
        found.guarded,
    )
