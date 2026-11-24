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
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import TYPE_CHECKING, Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import name_key
from dmbot.memory.scan import Suggestion
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
    consenting: Callable[[], Awaitable[Collection[int]]] | None = None,
    extra: Sequence[Suggestion] = (),
    asked: set[str] | None = None,
    left_out: set[str] | None = None,
    fixed_keys: set[str] | None = None,
) -> np.Report:
    """`extra`: the name reader's spellings, joined to the scan's candidates (slice 4, #5133).
    `asked`, when given, gets the key of every spelling the pass settled: written as a list name,
    or asked of the DM as unsure or new (`_keep_name_asks`), so the name step leaves those to it.
    Filled only once the pass has finished: a pass that fails part way leaves the name step's
    questions as they were. A "sure" answer that wrote nothing (a hidden name in the way) is not
    in it, so the card still asks. `left_out` gets the keys past its limit, which the card keeps;
    `fixed_keys` the spellings written over, which the step no longer offers."""
    texts = await pass_lines(store, guild_id, session_id, agreed, game_only)
    found = np.survey(texts, lookup, people, extra)
    if found.left_out:
        log.info("Name pass: %d spelling(s) past the limit, left to the DM", len(found.left_out))
    if not found.items:
        return np.Report(guarded=found.guarded)
    # The words PlotBot was not sure of, and the ones it says are new names, are offered to the DM
    # as name questions: what the campaign knows already (`known`, its keys) and the people at the
    # table (`people`) are not, by the same rule as the name scan. Counted here, so the live pass
    # and dev1's tool print the same numbers (#2655).
    skip = set(known) | set(people)
    reply = await ask(np.SYSTEM, np.question(found.items))
    stopped = False
    if agreed is not None and consenting is not None:
        # Consent is read again after the AI call (CLAUDE.md): a person who withdrew while it was
        # answering has none of their lines written.
        before = set(agreed)
        agreed = before & set(await consenting())
        stopped = agreed != before
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
    unsure, new = list(verdict.unsure), list(verdict.new)
    if stopped:
        # (#5162 review: a word heard only in the lines of someone who stopped is no question for
        # the DM. The words asked about include the name reader's, read before the pass, so the
        # lines of the people still recorded are read again and only a word said in them is kept.)
        still = await pass_lines(store, guild_id, session_id, agreed, game_only)

        def said(c: np.Candidate) -> bool:
            return any(contains(t, w) for w in (c.heard, *c.also) for t in still)

        unsure = [c for c in unsure if said(c)]
        new = [c for c in new if said(c)]
        log.info(
            "Name pass: a speaker stopped; %d question(s) dropped",
            len(verdict.unsure) + len(verdict.new) - len(unsure) - len(new),
        )  # counts only
    report = np.Report(
        tuple(fixed),
        tuple(c for c in unsure if name_key(c.heard) not in skip),
        tuple(c for c in new if name_key(c.heard) not in skip),
        found.guarded,
        cut,
    )
    # (slice 4, #5133: only now, after every write, what the pass settled is told to the step)
    written = {name_key(form) for f in fixed for form, _n in f.forms}
    if fixed_keys is not None:
        fixed_keys.update(written)
    if asked is not None:
        asked.update(written)
        asked.update(name_key(s) for c in (*unsure, *new) for s in (c.heard, *c.also))
    if left_out is not None:  # (past the limit: the name step's card asks the DM instead)
        left_out.update(found.left_out)
    return report
