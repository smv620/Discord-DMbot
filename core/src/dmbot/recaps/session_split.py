"""The split of one session into talk, catch-up, last time's recap and play (#3298), made once for
the session and kept on it, so the recap writer and the story reader share one answer and the AI
is asked once (never twice).

`recaps/split.py` (#2253) holds the prompt, the schema and the checks. The recap job used to be the
only caller, inside its own stage, with the job's call count and error budget. The story reader has
no job, so this module is the same labelling without the job: `split_for` reads the kept split
first; with none, it makes the one fast-tier call, checks the answer against the earlier session's
words exactly as the job does, and keeps the result (first writer wins). Anything unsure or wrong
is used for that read only and not kept, so the next read tries again: extraction is never
blocked on it, and a line is never dropped on a guess. Counts only in logs, never a word of a
transcript."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any, Protocol

from dmbot import ai_structured
from dmbot.ai import Reply
from dmbot.memory.extractor import Said, readable
from dmbot.recaps import split as sp
from dmbot.recaps.pipeline import SPLIT_TOKENS, Client, Inputs, unusable

log = logging.getLogger(__name__)

Consenting = Callable[[], Awaitable[Collection[int]]]
LoadInputs = Callable[[int, str, int], Awaitable[Inputs | None]]


class SplitStore(Protocol):
    """The two calls on the session (`TranscriptStore`) this module makes."""

    async def session_split(self, guild_id: int, session_id: str) -> dict[str, Any] | None: ...

    async def save_session_split(
        self, guild_id: int, session_id: str, split: Any, *, only_if_empty: bool = True
    ) -> bool: ...

    async def session_at(self, guild_id: int, campaign_id: str, started_at: int) -> Any: ...


class SessionSplitter:
    def __init__(
        self,
        store: SplitStore,
        load: LoadInputs,
        fast: Client,
        careful: Client,
        *,
        structured: bool = False,
    ) -> None:
        self._store = store
        self._load = load  # (guild, campaign, session start) -> the recap's inputs, or None
        self._fast, self._careful = fast, careful
        self._structured = structured  # RECAP_STRUCTURED_OUTPUT_SPLIT, as the recap job has it

    async def stored(self, guild_id: int, session_id: str) -> sp.Split | None:
        """The kept split, or None when there is none or it cannot be read."""
        try:
            return sp.from_json(await self._store.session_split(guild_id, session_id))
        except Exception as exc:
            log.warning("Session split: the kept split could not be read (%s)", type(exc).__name__)
            return None

    async def split_for(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        session_at: int,
        consenting: Consenting,
    ) -> sp.Split | None:
        """The session's split: the kept one, else made now and kept. None when it cannot be made
        (no inputs, no lines): the caller reads every line as play. Never raises."""
        try:
            # The session must be this campaign's (a split is kept by the session's id alone)
            found = await self._store.session_at(guild_id, campaign_id, session_at)
            if found is None or str(found.id) != session_id:
                return None
            kept = await self.stored(guild_id, session_id)
            if kept is not None:
                return kept
            inputs = await self._load(guild_id, campaign_id, session_at)
            if inputs is None:
                return None
            agreed = set(await consenting())
            said = [s for s in readable(inputs.lines) if s.user_id in agreed]
            if not said:
                return None
            split = await self._make(said, inputs, consenting)
            if split is None:  # a failed call: all play for now, tried again next time
                return None
            if split.method not in ("ai", sp.RULE) or split.unsure:
                # Only an answer the AI gave and the code backed up, or a spoken recap the rule
                # found (#3298: the DM sees its count and can undo it), is kept for good. A cut or
                # unreadable answer, a doubt, or the code's own guess is used for this read and
                # asked again next time, so one bad reply never fixes the session's split.
                return split
            await self._store.save_session_split(guild_id, session_id, split.to_json())
            # (the first writer wins: read back what is kept, which is ours or a racer's)
            return await self.stored(guild_id, session_id) or split
        except Exception as exc:  # the AI service, the network, the database: never fatal
            log.warning("Session split: not made (%s)", type(exc).__name__)
            return None

    async def _make(
        self, said: Sequence[Said], inputs: Inputs, consenting: Consenting
    ) -> sp.Split | None:
        """The labelling of `pipeline._split`, without the job: None if a call failed or someone
        stopped being recorded while it ran."""
        earlier = inputs.earlier
        if earlier is None:
            # No earlier session to check a retelling against: only a spoken recap code can see
            # for itself is marked (#3298), anything else is play. No call either way.
            return sp.spoken_recap(said, inputs.dm_ids) or sp.all_play(said)
        region = sp.opening_of(said)
        rows = [
            (str(s.number), "DM" if s.user_id in inputs.dm_ids else "Player", s.text)
            for s in region
        ]
        message = sp.message(rows, earlier, inputs.previous)
        labelled: list[tuple[str, int]] | None = None
        if self._structured:
            tried = await ai_structured.structured(
                sp.draft_system(),
                message,
                sp.SCHEMA,
                sp.parse,
                draft=self._careful,
                fmt=self._fast,
                escalate=self._careful,
                stage="split",
            )
            labelled = tried.value
        if labelled is None:
            reply: Reply = await self._fast.complete(
                sp.system(), message, max_tokens=SPLIT_TOKENS, schema=sp.SCHEMA
            )
            labelled = None if unusable(reply) else sp.parse(reply.text)
        if not {s.user_id for s in region} <= set(await consenting()):  # after the call
            return None
        split = (
            sp.without_ai(said, earlier, inputs.previous)
            if labelled is None
            else sp.build(said, labelled, earlier, inputs.previous)
        )
        log.info(
            "Session split: by %s into %s%s",
            split.method,
            {label: len(sp.pick(said, split, label)) for label in sp.LABELS},
            ", the retelling unsure" if split.unsure else "",
        )
        return split


async def release_recap_lines(store: Any, guild_id: int, session_id: str) -> frozenset[int]:
    """The DM says last time's recap was play after all (#3298): the lines the kept split labels
    as recap become play (method "dm"; talk and catch-up stay what they were) and are queued to be
    read again, in one transaction (`TranscriptStore.save_split_and_queue`). Returns the line
    numbers released, none when there was no recap to hand back or the kept split cannot be read.
    This changes what later reads do; an earlier read does not re-read them by itself (a read
    resumes after `extracted_through_ms`), which is why they are queued as put-back lines."""
    stored = sp.from_json(await store.session_split(guild_id, session_id))
    if stored is None or not stored.has(sp.RECAP):
        return frozenset()
    released = frozenset(
        n for p in stored.parts if p.label == sp.RECAP for n in range(p.first, p.last + 1)
    )
    play = sp.Split(
        tuple(
            sp.Part(sp.PLAY if p.label == sp.RECAP else p.label, p.first, p.last)
            for p in stored.parts
        ),
        False,
        "dm",
    )
    await store.save_split_and_queue(guild_id, session_id, play.to_json(), released)
    return released


__all__ = ["SessionSplitter", "release_recap_lines"]
