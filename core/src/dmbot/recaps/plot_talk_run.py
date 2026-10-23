"""The bot's side of the DM's talk with PlotBot about an issue (#2123 slice 2).

The website has no AI key, so the DM's message waits as a row (`plot_chat`); this module claims it,
gathers what the call needs (the issue, the recap, the earlier turns and the few campaign entries
the talk is about), makes one careful-tier call and writes PlotBot's answer as a second row. The
words are the DM's own and are never logged (counts and types only). A claim that stalled is taken
again; a message that cannot be answered is marked failed with a short code, and the DM can send
it again. A request to invent the story gets the plain no with no call. Nothing is saved to the
campaign here: a proposal is data until the DM presses Save (slice 3).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.memory import plot_talk as pt
from dmbot.memory.models import CONFIRMED
from dmbot.memory.ontology import conditions_for

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
CALL_TIMEOUT_S = RETAKE_AFTER_S - 30  # the call always ends before its claim can be taken back
PER_ROUND = 5  # messages answered in one round in one server


class Completer(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Any: ...


class Memory(Protocol):
    async def entities(
        self, guild_id: int, campaign_id: str, *, statuses: Sequence[str]
    ) -> Any: ...
    async def states(self, guild_id: int, campaign_id: str, entity_ids: Any = ...) -> Any: ...
    async def ontology(self, guild_id: int, campaign_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    campaign_id: str
    session_at: int
    issue_id: str
    user_id: int
    text: str
    claimed_at: int  # this worker's claim: only it may finish the row


class TalkStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Message | None:
        """The oldest waiting message (or a stalled one), marked as being worked on."""
        now = int(self._now() * 1000)  # milliseconds: the claim is the token that lets one write
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE plot_chat SET state = 'working', claimed_at = %s WHERE (guild_id, id) ="
                " (SELECT guild_id, id FROM plot_chat WHERE guild_id = %s AND role = 'dm' AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, session_at, issue_id, user_id, body",
                (now, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Message(
            row["id"], row["campaign_id"], int(row["session_at"]), row["issue_id"],
            int(row["user_id"]), row["body"], now,
        )  # fmt: skip

    async def context_rows(
        self, guild_id: int, m: Message
    ) -> tuple[dict[str, Any], str, list[pt.Turn]] | None:
        """(the issue, the recap's text, the earlier turns) for this message, or None when the
        issue or the recap is gone or the recap has changed since the issue was found."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT i.kind, i.level, i.sentence_text, i.reason, i.source_text,"
                " i.source_session, i.text_hash, j.title, j.summary,"
                " encode(sha256(convert_to(j.title || E'\\n' || j.summary, 'UTF8')), 'hex') AS now"
                " FROM plot_issues i JOIN recap_jobs j ON j.guild_id = i.guild_id"
                " AND j.campaign_id = i.campaign_id AND j.session_at = i.session_at"
                " WHERE i.guild_id = %s AND i.campaign_id = %s AND i.id = %s AND i.session_at = %s",
                (guild_id, m.campaign_id, m.issue_id, m.session_at),
            )
            issue = await cur.fetchone()
            if issue is None or issue["text_hash"] != issue["now"]:
                return None
            cur = await conn.execute(
                "SELECT role, body FROM plot_chat WHERE guild_id = %s AND campaign_id = %s"
                " AND issue_id = %s AND user_id = %s AND id <> %s AND state = 'done'"
                " ORDER BY created_at, role DESC, id",  # (one message at a time: all are earlier)
                (guild_id, m.campaign_id, m.issue_id, m.user_id, m.id),
            )
            turns = [pt.Turn(r["role"], r["body"]) for r in await cur.fetchall()]
        return dict(issue), f"{issue['title']}\n{issue['summary']}", turns

    async def finish(self, guild_id: int, m: Message, answer: pt.Answer) -> bool:
        """Write PlotBot's answer and close the message, together, only if this worker still holds
        the claim. False: it was not this worker's any more."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE plot_chat SET state = 'done' WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (guild_id, m.id, m.claimed_at),
            )
            if not cur.rowcount:
                return False
            await conn.execute(
                "INSERT INTO plot_chat (guild_id, campaign_id, id, issue_id, session_at, user_id,"
                " role, body, state, proposals, created_at) VALUES (%s, %s, %s, %s, %s, %s, 'bot',"
                " %s, 'done', %s, %s)",
                (
                    guild_id,
                    m.campaign_id,
                    uuid.uuid4().hex,
                    m.issue_id,
                    m.session_at,
                    m.user_id,
                    answer.text,
                    Jsonb([p.as_json() for p in answer.proposals]),
                    now + 1,
                ),
            )  # fmt: skip (created a second later so the answer sorts after its question)
            return True

    async def fail(self, guild_id: int, m: Message, error: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET state = 'failed', error = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (error[:40], guild_id, m.id, m.claimed_at),
            )


def _mentions(name: str, haystack: str) -> bool:
    return name.casefold() in haystack.casefold()


class TalkRunner:
    def __init__(self, store: TalkStore, memory: Memory, ai: Completer | None) -> None:
        self._store = store
        self._memory = memory
        self._ai = ai

    async def round(self, guild_id: int, *, limit: int = PER_ROUND) -> int:
        """Answer the messages that wait in one server, at most `limit`. Returns how many."""
        done = 0
        for _ in range(limit):
            m = await self._store.claim_next(guild_id)
            if m is None:
                break
            done += 1
            await self._answer(guild_id, m)
        return done

    async def _entries(self, guild_id: int, campaign_id: str, about: str) -> list[pt.Entry]:
        """The campaign entries the talk is about: those whose name appears in its words."""
        found = [
            e
            for e in await self._memory.entities(guild_id, campaign_id, statuses=(CONFIRMED,))
            if len(e.name) >= 3 and _mentions(e.name, about)
        ][: pt.MAX_ENTRIES]
        if not found:
            return []
        onto = await self._memory.ontology(guild_id, campaign_id)
        held = await self._memory.states(guild_id, campaign_id, [e.id for e in found])
        out: list[pt.Entry] = []
        for e in found:
            conditions = conditions_for(onto, e.type)
            if conditions:
                state = held.get(e.id)
                out.append(
                    pt.Entry(e.id, e.name, e.type, state.condition if state else "", conditions)
                )
        return out

    async def _answer(self, guild_id: int, m: Message) -> None:
        try:
            plain = pt.refusal_for(m.text)
            if plain is not None:  # the plain no: code decides, no call is made
                await self._store.finish(guild_id, m, plain)
                return
            rows = await self._store.context_rows(guild_id, m)
            if rows is None:
                return await self._store.fail(guild_id, m, "stale")
            issue, recap, turns = rows
            if self._ai is None:
                return await self._store.fail(guild_id, m, "unavailable")
            about = " ".join(
                [issue["sentence_text"], issue["source_text"], m.text, *[t.text for t in turns]]
            )
            ctx = pt.Context(
                pt.Issue(
                    issue["kind"], issue["level"], issue["sentence_text"], issue["reason"],
                    issue["source_text"], issue["source_session"] or "",
                ),
                recap, m.session_at, turns, m.text,
                await self._entries(guild_id, m.campaign_id, about),
            )  # fmt: skip
            reply = await asyncio.wait_for(
                self._ai.complete(pt.system(), pt.message(ctx), max_tokens=pt.TOKENS),
                CALL_TIMEOUT_S,
            )
            if getattr(reply, "refused", False) or getattr(reply, "cut", False):
                return await self._store.fail(guild_id, m, "no_answer")
            answer = pt.parse(reply.text, ctx)
            if answer is None:
                return await self._store.fail(guild_id, m, "bad_answer")
            await self._store.finish(guild_id, m, answer)
        except Exception as exc:  # the type only: an AI or database error can quote a row
            log.warning("A plot talk answer failed: %s", type(exc).__name__)
            try:
                await self._store.fail(guild_id, m, "failed")
            except Exception:
                log.warning("A plot talk answer could not be marked failed")
