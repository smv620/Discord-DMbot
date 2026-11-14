"""Writing the scenes of a session's waiting questions (#3596 slice 1, PR 3): the step that runs
after a session. It gathers the new questions that have no scene, reads the lines each came from
(the pointers `Where was it said?` shows), asks the AI for all of them together, and keeps the
scenes it gets.

Held here, not trusted:
- **Inside the extractor's call limit.** Each call is counted before it is made (`count_call`) and
  the step stops at `max_calls`, so turning scenes on does not raise what a session may spend.
- **Consent after every wait.** Only lines of people recorded now are read (the transcript read
  itself filters to them), who is recorded is asked again right before each call, and again after
  the answer: a scene whose lines include someone who has stopped is dropped.
- **Never fatal.** A failed call is counted and the rest go on; out of funds stops the step; a
  cut answer keeps the scenes it has and counts the rest as stopped early.
- **Only lines already shown to the DM's own card.** A question with no lines left (a transcript
  the retention rule deleted, or a person made without session lines) gets no scene and is counted.
Counts only leave this module; the words of a transcript and of a scene are never logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import scene_reading as sr
from dmbot.memory.models import PROPOSED, Entity
from dmbot.memory.scenes import ENTITY, Scene, Scenes
from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 300.0  # above the slowest tier's own request limit, as the extractor's
Consenting = Callable[[], Awaitable[Collection[int]]]


@dataclass(slots=True)
class Report:
    """What the step came to. Counts only."""

    questions: int = 0  # waiting questions with no scene yet
    no_lines: int = 0  # ... of which no line was left to read
    calls: int = 0
    failed: int = 0
    made: int = 0
    refused: int = 0
    unasked: int = 0
    stopped_early: int = 0  # items in answers that were cut short and never came back
    dropped_for_consent: int = 0
    unsent: int = 0  # questions left for next time: the call limit, or the run stopped
    stopped: str = ""

    def line(self) -> str:
        return (
            f"Scenes: {self.questions} question(s), {self.made} made, {self.no_lines} with no "
            f"lines, {self.refused} refused, {self.unasked} unasked, {self.stopped_early} stopped "
            f"early, {self.dropped_for_consent} dropped for consent, {self.calls} call(s), "
            f"{self.failed} failed, {self.unsent} left for next time"
            + (f", stopped: {self.stopped}" if self.stopped else "")
        )


def parse_ref(ref: str) -> tuple[str, int, int] | None:
    """A line reference (`transcript.models.line_ref`: session, when the line began, who said it)
    back into its parts."""
    parts = ref.rsplit(":", 2)
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


def is_open_name(entity: Entity) -> bool:
    """A suggestion (a name to check) or a person the story never named, not merged away."""
    return entity.merged_into is None and (
        entity.status == PROPOSED or (entity.status == "confirmed" and entity.unnamed)
    )


async def items_for(
    store_entities: Sequence[Entity],
    mentions: Callable[[str], Awaitable[list[tuple[str, int | None]]]],
    transcripts: TranscriptStore,
    guild_id: int,
    dm_ids: Collection[int],
    agreed: Collection[int],
    *,
    first_number: int = 1,
) -> tuple[list[sr.SceneItem], int]:
    """The items to ask about for these questions (numbered from `first_number`), and how many had
    no line to read. A question's lines are its pointers' lines with the one before and after,
    from people who agree now."""
    items: list[sr.SceneItem] = []
    empty = 0
    number = first_number
    for entity in store_entities:
        lines: list[sr.SceneLine] = []
        speakers: set[int] = set()
        for ref, _at in (await mentions(entity.id))[: sr.NAME_LINES_MAX]:
            pointer = parse_ref(ref)
            if pointer is None:
                continue
            around = await transcripts.around(guild_id, pointer[0], pointer[1], pointer[2], agreed)
            if around is None:
                continue
            for hit, seen in ((False, around.before), (True, around.line), (False, around.after)):
                if seen is not None:
                    user, text = seen
                    lines.append(sr.SceneLine(user in dm_ids, text, hit))
                    speakers.add(user)
        if not lines:
            empty += 1
            continue
        kind = sr.PERSON if entity.unnamed else sr.NAME
        items.append(
            sr.SceneItem(
                number, kind, ENTITY, entity.id, sr.weigh(lines, kind), tuple(sorted(speakers))
            )
        )
        number += 1
    return items, empty


async def run(
    client: _Client,
    scenes: Scenes,
    items: Sequence[sr.SceneItem],
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
    """Ask for the scenes of `items` (already holding only lines of people who agreed) in as few
    calls as fit, and keep what comes back."""
    report = report if report is not None else Report()
    report.questions += len(items)
    parts = sr.chunks(items)
    for index, part in enumerate(parts):
        if report.calls >= max_calls:
            report.stopped = report.stopped or "call limit"
            report.unsent += sum(len(p) for p in parts[index:])
            break
        try:
            agreed = set(await consenting())
        except Exception as exc:  # who is recorded can't be known: nothing is sent
            log.warning("Scenes: couldn't ask who is recorded (%s)", type(exc).__name__)
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
                log.warning("Scenes: couldn't count a call (%s)", type(exc).__name__)
                report.failed += 1
                report.unsent += len(sendable)
                continue
        report.calls += 1
        try:
            reply = await asyncio.wait_for(
                client.complete(sr.SCENE_SYSTEM, sr.items_text(sendable), max_tokens=16_000),
                CALL_TIMEOUT_S,
            )
        except AIOutOfFunds:
            report.failed += 1
            report.stopped = "out of funds"
            report.unsent += sum(len(p) for p in parts[index + 1 :])
            break
        except Exception as exc:  # never fatal: the other parts go on
            log.warning("Scenes: a call failed (%s)", type(exc).__name__)
            report.failed += 1
            report.unsent += len(sendable)
            continue
        got = sr.parse_scenes(reply.text, [i.number for i in sendable], cut=reply.cut)
        report.refused += got.refused
        report.unasked += got.unasked
        if got.stopped_early:
            report.stopped_early += max(0, len(sendable) - len(got.scenes) - got.refused)
        by_number = sr.numbered(sendable)
        try:
            still = set(await consenting())  # asked again after the answer
        except Exception as exc:  # can't know who is recorded now: keep none of this part
            log.warning("Scenes: couldn't ask who is recorded (%s)", type(exc).__name__)
            report.failed += 1
            report.dropped_for_consent += len(got.scenes)
            continue
        keep: list[Scene] = []
        for number, text in got.scenes.items():
            item = by_number[number]
            if not set(item.speaker_ids) <= still:
                report.dropped_for_consent += 1
                continue
            keep.append(
                Scene(
                    item.subject_kind,
                    item.subject_id,
                    text,
                    len(item.lines),
                    item.speaker_ids,
                    session_started_at,
                )
            )
        try:
            report.made += await scenes.keep(
                guild_id,
                campaign_id,
                keep,
                opted_in=still,
                prompt_version=sr.SCENE_PROMPT_VERSION,
                model=reply.model or model(),
            )
        except Exception as exc:  # only the type: the rows hold the scene's words
            log.warning("Scenes: storing failed (%s)", type(exc).__name__)
            report.failed += 1
    return report


class _Client(Protocol):
    """What `run` needs of the AI client (the extractor's)."""

    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...
