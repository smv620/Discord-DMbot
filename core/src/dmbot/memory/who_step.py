"""Working out who a waiting name is (#3602, slice 2c): the step that runs after a session. It takes
the names still waiting on "Who is this?", offers each the few entries it could be (slice 1's
ranking), asks the AI for all of them together (slice 2a's question and reader) and keeps the
ranked answers (slice 2b's table). It only ranks: nothing here changes the story, and nothing is
applied (slice 4, off by default).

Held here, not trusted:
- **One campaign per call.** The caller passes one campaign's names and one campaign's entries; a
  call never mixes two (`Pending.refs` maps each choice back to an entry of that campaign only).
- **Inside the extractor's call limit.** Each call is counted before it is made (`count_call`) and
  the step stops at `max_calls`, so turning it on does not raise what a session may spend. The
  answer may be long, so the call asks for the background floor of output, never a hand-sized cap
  (CLAUDE.md "AI output limits are generous"); a cut answer keeps the whole items it has and counts
  the rest as stopped early, never as "nothing found".
- **Consent after every wait.** Who is recorded is asked before each call (only a name whose lines
  all come from people recorded now is sent) and again after the answer: an answer worked out from
  someone who has stopped is dropped, and the table refuses it too.
- **The prompt version is stored with every answer** (`who_reading.WHO_PROMPT_VERSION`).
- **Hidden entries only in the DM's ranking.** `candidates_from` builds each choice's names from the
  campaign lookup: a visible entry's secret names never reach a choice, and an entry hidden from the
  players is a choice only for the DM's own ranking (`include_hidden`), with all its names.
- **Never fatal.** A failed call is counted and the rest go on; out of funds stops the step.
Counts only leave this module; no name, scene or line is logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.ai import OUTPUT_FLOOR, AIOutOfFunds, Reply
from dmbot.memory import who_ranking as ranking
from dmbot.memory import who_reading as reading
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import PROPOSED, Entity
from dmbot.memory.who_answers import ENTITY, MAX_RANK, NEW, StoredAnswer, WhoAnswers

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 300.0  # above the slowest tier's own request limit, as the extractor's
BACKGROUND_FLOOR = OUTPUT_FLOOR  # background calls never send less than the tier floor
Consenting = Callable[[], Awaitable[Collection[int]]]


@dataclass(slots=True)
class Report:
    """What the step came to. Counts only."""

    names: int = 0  # waiting names looked at
    no_choices: int = 0  # ... of which nothing could be offered (the caller counts these: `build`)
    calls: int = 0
    failed: int = 0
    ranked: int = 0  # names with at least one answer kept
    empty: int = 0  # names the AI gave no usable answer for (it may overlap `dropped`)
    omitted: int = 0  # names the AI's whole reply left out
    refused: int = 0  # calls the service declined (the text is not an answer)
    not_kept: int = 0  # answers the store would not keep (malformed, or the name is not an entry)
    dropped: int = 0  # answers dropped (off the lists, not offered, odd rows)
    stopped_early: int = 0
    dropped_for_consent: int = 0
    unsent: int = 0  # names left for next time: the call limit, or the run stopped
    stopped: str = ""

    def line(self) -> str:
        return (
            f"Who is this: {self.names} name(s), {self.ranked} ranked, {self.empty} with no "
            f"answer, {self.omitted} left out, {self.no_choices} with no choice, "
            f"{self.refused} refused, {self.not_kept} not kept, {self.dropped} answer(s) dropped, "
            f"{self.stopped_early} stopped early, {self.dropped_for_consent} dropped for consent, "
            f"{self.calls} call(s), {self.failed} failed, {self.unsent} left for next time"
            + (f", stopped: {self.stopped}" if self.stopped else "")
        )


@dataclass(frozen=True, slots=True)
class Pending:
    """One waiting name, ready to ask: the item sent, which entry each choice stands for (never
    sent), and the people whose lines it was worked out from."""

    entity_id: str
    item: reading.WhoItem
    refs: dict[str, str] = field(default_factory=dict)  # "c1" -> the entry's id


def is_open_name(entity: Entity) -> bool:
    """A suggestion (a name to check), not merged away. An unnamed person is asked another way."""
    return entity.merged_into is None and entity.status == PROPOSED and not entity.unnamed


def candidates_from(
    lookup: CampaignLookup, *, include_hidden: bool, leave_out: Collection[str] = ()
) -> list[ranking.Candidate]:
    """The entries a name could be, from one campaign's lookup (read for the DM: its `hidden`
    set is who may not be told). A visible entry's names are its main name and its other names that
    are not secret, and its ties only the visible ones; a secret name never reaches a visible
    entry's choice (that would confirm it). A hidden entry is a choice only with `include_hidden`
    (the DM's own ranking), and then carries every name it has. Only confirmed entries; `leave_out`
    (the waiting names themselves) are never choices."""
    out: list[ranking.Candidate] = []
    skip = set(leave_out)
    for entity in lookup.entities.values():
        if entity.id in skip or entity.status != "confirmed" or entity.unnamed:
            continue
        if entity.merged_into is not None:
            continue
        hidden = entity.id in lookup.hidden
        if hidden and not include_hidden:
            continue
        names = [entity.name]
        for n in lookup.names:
            if n.entity_id != entity.id or n.text == entity.name:
                continue
            if n.secret and not hidden:
                continue  # a visible entry's secret name stays out of the choice
            if not n.confirmed:
                continue  # a name DMbot only guessed is not offered as one of the entry's
            names.append(n.text)
        ties = frozenset(
            t for t in lookup.confirmed_neighbours.get(entity.id, frozenset())
            if t not in lookup.hidden
        )  # fmt: skip
        out.append(
            ranking.Candidate(entity.id, entity.type, tuple(dict.fromkeys(names)), ties, hidden)
        )
    return out


def build(
    entity: Entity,
    candidates: Sequence[ranking.Candidate],
    *,
    number: int,
    scene: str = "",
    lines: Sequence[str] = (),
    speaker_ids: Collection[int] = (),
    scene_speaker_ids: Collection[int],
    scene_ids: Collection[str] = (),
    session_ids: Collection[str] = (),
    owner_ids: Collection[str] = (),
    include_hidden: bool,
) -> Pending | None:
    """The question for one waiting name, or None when nothing can be offered (or when session
    lines would be sent with no one named to check consent for). The choices are the best five by
    `who_ranking.rank`, labelled c1, c2, ... for this item only. `speaker_ids` are the people whose
    lines are sent and `scene_speaker_ids` those the scene was made from (`Scene.speaker_ids`): the
    item carries both, so the consent check covers every word sent. A name read from the DM's own
    document has no session lines and no speakers, and is sent as it is."""
    speakers = tuple(sorted({*speaker_ids, *scene_speaker_ids}))
    if lines and not speaker_ids:
        return None
    ask = ranking.Ask(
        entity.name,
        entity.type,
        frozenset(scene_ids),
        frozenset(session_ids),
        frozenset(owner_ids),
    )
    best = ranking.rank(ask, candidates, include_hidden=include_hidden)
    if not best:
        return None
    refs: dict[str, str] = {}
    choices: list[reading.Choice] = []
    for n, ranked in enumerate(best, 1):
        ref = f"c{n}"
        refs[ref] = ranked.candidate.id
        choices.append(
            reading.Choice(
                ref,
                ranked.candidate.kind,
                ranked.candidate.names,
                ranked.reasons,
                ranked.candidate.hidden,
            )
        )
    item = reading.WhoItem(
        number, entity.name, entity.type, scene, tuple(lines), speakers, tuple(choices)
    )
    return Pending(entity.id, item, refs)


def to_stored(answers: Sequence[reading.Answer], refs: dict[str, str]) -> list[StoredAnswer]:
    """The AI's answers as the table keeps them: a choice becomes the entry it stands for."""
    out: list[StoredAnswer] = []
    for a in list(answers)[:MAX_RANK]:  # (the store keeps at most this many)
        if a.pick == reading.NEW:
            out.append(StoredAnswer(NEW, a.confidence, new_kind=a.kind))
        elif a.pick in refs:
            out.append(StoredAnswer(ENTITY, a.confidence, target_id=refs[a.pick]))
    return out


async def run(
    client: _Client,
    answers: WhoAnswers,
    pending: Sequence[Pending],
    *,
    guild_id: int,
    campaign_id: str,
    consenting: Consenting,
    model: Callable[[], str],
    session_started_at: int | None,
    max_calls: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
    report: Report | None = None,
) -> Report:
    """Ask who each of `pending` is (one campaign's, already holding only lines of people who
    agreed) in as few calls as fit, and keep the ranked answers that come back."""
    report = report if report is not None else Report()
    report.names += len(pending)
    items = [p.item for p in pending]
    by_number = {p.item.number: p for p in pending}
    if len(by_number) != len(pending):
        raise ValueError("Each question needs its own number.")
    parts = reading.chunks(items)
    for index, part in enumerate(parts):
        if report.calls >= max_calls:
            report.stopped = report.stopped or "call limit"
            report.unsent += sum(len(p) for p in parts[index:])
            break
        try:
            agreed = set(await consenting())
        except Exception as exc:  # who is recorded can't be known: nothing is sent
            log.warning("Who is this: couldn't ask who is recorded (%s)", type(exc).__name__)
            report.failed += 1
            report.unsent += len(part)
            continue
        sendable = [i for i in part if set(i.speaker_ids) <= agreed]
        report.dropped_for_consent += len(part) - len(sendable)
        if not sendable:
            continue
        if count_call is not None:
            try:
                await count_call()
            except Exception as exc:  # the budget is a hard limit: an uncounted call is not made
                log.warning("Who is this: couldn't count a call (%s)", type(exc).__name__)
                report.failed += 1
                report.unsent += len(sendable)
                continue
        report.calls += 1
        try:
            reply = await asyncio.wait_for(
                client.complete(
                    reading.WHO_SYSTEM, reading.items_text(sendable), max_tokens=BACKGROUND_FLOOR
                ),
                CALL_TIMEOUT_S,
            )
        except AIOutOfFunds:
            report.failed += 1
            report.stopped = "out of funds"
            report.unsent += sum(len(p) for p in parts[index + 1 :])
            break
        except Exception as exc:  # never fatal: the other parts go on
            log.warning("Who is this: a call failed (%s)", type(exc).__name__)
            report.failed += 1
            report.unsent += len(sendable)
            continue
        if reply.refused:  # the service declined: its text is not an answer
            report.refused += 1
            report.empty += len(sendable)
            continue
        got = reading.parse_answers(reply.text, sendable, cut=reply.cut)
        report.dropped += sum(got.dropped.values())
        left_out = max(0, len(sendable) - len(got.answers))
        if got.stopped_early:
            report.stopped_early += left_out
        else:
            report.omitted += left_out
        try:
            still = set(await consenting())  # asked again after the answer
        except Exception as exc:  # can't know who is recorded now: keep none of this part
            log.warning("Who is this: couldn't ask who is recorded (%s)", type(exc).__name__)
            report.failed += 1
            report.dropped_for_consent += len(got.answers)
            continue
        for number, ranked in got.answers.items():
            p = by_number[number]
            if not set(p.item.speaker_ids) <= still:
                report.dropped_for_consent += 1
                continue
            stored = to_stored(ranked, p.refs)
            if not stored:
                report.empty += 1
                continue
            try:
                kept = await answers.keep(
                    guild_id,
                    campaign_id,
                    p.entity_id,
                    stored,
                    speaker_ids=p.item.speaker_ids,
                    opted_in=still,
                    session_started_at=session_started_at,
                    prompt_version=reading.WHO_PROMPT_VERSION,
                    model=reply.model or model(),
                )
            except Exception as exc:  # only the type: the rows hold ids and words
                log.warning("Who is this: storing failed (%s)", type(exc).__name__)
                report.failed += 1
                continue
            if kept:
                report.ranked += 1
            else:
                report.not_kept += 1  # (consent was checked above: the store found it malformed)
    try:
        await answers.sweep(guild_id, campaign_id)  # a name answered during the wait goes now
    except Exception as exc:
        log.warning("Who is this: sweeping failed (%s)", type(exc).__name__)
    return report


class _Client(Protocol):
    """What `run` needs of the AI client (the extractor's)."""

    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...
