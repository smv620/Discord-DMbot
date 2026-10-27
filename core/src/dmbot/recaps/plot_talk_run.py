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
import re
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.memory import plot_talk as pt
from dmbot.memory._changes import ENTITIES, STATES, Changes
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError
from dmbot.memory.ontology import conditions_for

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
CALL_TIMEOUT_S = RETAKE_AFTER_S - 30  # the call always ends before its claim can be taken back
PER_ROUND = 5  # messages answered in one round in one server
GIVE_UP_S = 600  # a message older than this is not retaken: a crash costs one call, not one a pass
KEEP_S = 30 * 24 * 3600  # a talk is kept this long, then goes


class Completer(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Any: ...


class Memory(Protocol):
    async def entities(
        self, guild_id: int, campaign_id: str, *, statuses: Sequence[str]
    ) -> Any: ...
    async def states(self, guild_id: int, campaign_id: str, entity_ids: Any = ...) -> Any: ...
    async def entity(self, guild_id: int, campaign_id: str, entity_id: str) -> Any: ...
    def _write(
        self, guild_id: int, campaign_id: str, source: str
    ) -> AbstractAsyncContextManager[Changes]: ...
    async def set_condition_in(
        self, w: Changes, entity_id: str, condition: str, from_session_at: int | None = ...
    ) -> Any: ...
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


@dataclass(frozen=True, slots=True)
class Saving:
    """A proposal the DM pressed Save on, with the message and issue it belongs to."""

    message_id: str
    campaign_id: str
    session_at: int
    issue_id: str
    user_id: int
    proposal: dict[str, Any]


class TalkStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def sweep(self, guild_id: int) -> int:
        """Delete the talks that are over thirty days old, in every campaign of the server (the
        send door clears a campaign's own as it is used; this reaches the ones never used again)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM plot_chat WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_S),
            )
            return int(cur.rowcount)

    async def claim_next(self, guild_id: int) -> Message | None:
        """The oldest waiting message (or a stalled one), marked as being worked on."""
        now = int(self._now() * 1000)  # milliseconds: the claim is the token that lets one write
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE plot_chat SET state = 'working', claimed_at = %s WHERE (guild_id, id) ="
                " (SELECT guild_id, id FROM plot_chat WHERE guild_id = %s AND role = 'dm' AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s"
                " AND created_at > %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, session_at, issue_id, user_id, body",
                (now, guild_id, now - RETAKE_AFTER_S * 1000, now // 1000 - GIVE_UP_S),
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
                "UPDATE plot_chat SET state = 'done' WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND state = 'working' AND claimed_at = %s",
                (guild_id, m.campaign_id, m.id, m.claimed_at),
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

    async def saving(self, guild_id: int, *, limit: int = PER_ROUND) -> list[Saving]:
        """The proposals the DM pressed Save on that the bot has not made yet, oldest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT m.id, m.campaign_id, m.session_at, m.issue_id, m.user_id, p.value AS p"
                " FROM plot_chat m, jsonb_array_elements(m.proposals) AS p(value)"
                " WHERE m.guild_id = %s AND m.role = 'bot' AND p.value->>'state' = 'saving'"
                " ORDER BY m.created_at, m.id LIMIT %s",
                (guild_id, limit),
            )
            return [
                Saving(
                    r["id"], r["campaign_id"], int(r["session_at"]), r["issue_id"],
                    int(r["user_id"]), dict(r["p"]),
                )
                for r in await cur.fetchall()
            ]  # fmt: skip

    async def mark(self, guild_id: int, s: Saving, state: str, error: str = "") -> bool:
        """Close a proposal as `failed` (with a short plain reason), on its own. Only one that is
        still `saving`: a second worker, or a repeat, changes nothing."""
        async with self._db.guild(guild_id) as conn:
            return await self.mark_in(conn, guild_id, s, state, error)

    async def mark_in(
        self,
        conn: Any,
        guild_id: int,
        s: Saving,
        state: str,
        error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """`mark` inside a transaction that is already open (the apply's). `extra` is merged into
        the proposal (the id of the state it became, which is its lineage)."""
        cur = await conn.execute(
            "UPDATE plot_chat SET proposals = (SELECT jsonb_agg(CASE WHEN e->>'id' = %s"
            " AND e->>'state' = 'saving' THEN e || jsonb_build_object('state', %s::text,"
            " 'error', %s::text) || %s ELSE e END ORDER BY ord)"
            " FROM jsonb_array_elements(proposals) WITH ORDINALITY AS t(e, ord))"
            " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND proposals @>"
            " jsonb_build_array(jsonb_build_object('id', %s::text, 'state', 'saving'))",
            (
                s.proposal["id"], state, error[:200], Jsonb(extra or {}), guild_id,
                s.campaign_id, s.message_id, s.proposal["id"],
            ),
        )  # fmt: skip
        return bool(cur.rowcount)

    async def lock_for_apply(self, conn: Any, guild_id: int, s: Saving) -> str | None:
        """Inside the apply's transaction: take the message's row and the recap's, and check what
        must still be true. None when all is well, else the plain reason it is not. A proposal
        that is no longer `saving` is "" (nothing to do)."""
        cur = await conn.execute(
            "SELECT 1 FROM plot_chat WHERE guild_id = %s AND campaign_id = %s AND id = %s"
            " AND proposals @> jsonb_build_array(jsonb_build_object('id', %s::text,"
            " 'state', 'saving')) FOR UPDATE",
            (guild_id, s.campaign_id, s.message_id, s.proposal["id"]),
        )
        if await cur.fetchone() is None:
            return ""
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, s.campaign_id, s.user_id),
        )
        if await cur.fetchone() is None:
            return "Only a DM of this campaign can."
        # The recap row is locked, so an edit waits until this save is done: the text checked is
        # the text the answer is made against (the same lock the website's answer takes).
        cur = await conn.execute(
            "SELECT 1 FROM plot_issues i JOIN recap_jobs j ON j.guild_id = i.guild_id"
            " AND j.campaign_id = i.campaign_id AND j.session_at = i.session_at"
            " WHERE i.guild_id = %s AND i.campaign_id = %s AND i.id = %s"
            " AND i.text_hash = encode(sha256(convert_to(j.title || E'\\n' || j.summary,"
            " 'UTF8')), 'hex') FOR UPDATE OF j",
            (guild_id, s.campaign_id, s.issue_id),
        )
        if await cur.fetchone() is None:
            return "The recap changed. Check it again."
        return None

    async def answer_changed_in(self, conn: Any, guild_id: int, s: Saving, note: str) -> bool:
        """Answer the issue "the story changed" (the DM's own answer, made by Save), unless the DM
        has answered it already or the recap's text is no longer the one the issue is about. The
        same point is no longer remembered as "not a problem"."""
        now = int(self._now())
        cur = await conn.execute(
            "UPDATE plot_issues i SET answer_kind = 'changed', answer_note = %s,"
            " answered_by = %s, answered_at = %s FROM recap_jobs j WHERE j.guild_id = i.guild_id"
            " AND j.campaign_id = i.campaign_id AND j.session_at = i.session_at"
            " AND i.guild_id = %s AND i.campaign_id = %s AND i.id = %s AND i.answer_kind IS NULL"
            " AND i.text_hash = encode(sha256(convert_to(j.title || E'\\n' || j.summary,"
            " 'UTF8')), 'hex') RETURNING i.fine_key",
            (note[:500], s.user_id, now, guild_id, s.campaign_id, s.issue_id),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        await conn.execute(
            "DELETE FROM plot_fine WHERE guild_id = %s AND campaign_id = %s AND fine_key = %s",
            (guild_id, s.campaign_id, row["fine_key"]),
        )
        return True

    async def fail(self, guild_id: int, m: Message, error: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET state = 'failed', error = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (error[:40], guild_id, m.campaign_id, m.id, m.claimed_at),
            )


def _mentions(name: str, haystack: str) -> bool:
    """Is the name in the words as a whole word ("Ann" is not in "channel")?"""
    return re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", haystack.casefold()) is not None


class TalkRunner:
    def __init__(self, store: TalkStore, memory: Memory, ai: Completer | None) -> None:
        self._store = store
        self._memory = memory
        self._ai = ai

    async def sweep(self, guild_id: int) -> int:
        return await self._store.sweep(guild_id)

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

    async def apply_round(self, guild_id: int, *, limit: int = PER_ROUND) -> int:
        """Make the changes the DM saved, at most `limit`. Returns how many were looked at."""
        done = 0
        for s in await self._store.saving(guild_id, limit=limit):
            done += 1
            await self._apply(guild_id, s)
        return done

    async def _apply(self, guild_id: int, s: Saving) -> None:
        """One saved proposal, as the DM's own fact, in ONE transaction (the pattern of
        `memory/claims.py`, #1301): the proposal is locked and still `saving`, the saver is still a
        DM, the recap is unchanged, the entry is a confirmed one of this campaign, the condition is
        set (the memory store's own: the old one closes and stays as history, and the rules of the
        table refuse what a kind cannot be), the proposal is marked `saved` with the id of the state
        it became, and the issue is answered. Any refusal rolls all of it back, and the proposal is
        then marked `failed` on its own. So a crash leaves nothing half done, and a retry never
        writes over what the DM changed by hand in between."""
        try:
            data = s.proposal.get("data") or {}
            entity_id, condition = data.get("entity_id"), data.get("condition")
            at = data.get("from_session_at")
            if not isinstance(entity_id, str) or not isinstance(condition, str):
                return await self._finish(
                    guild_id, s, "failed", "That change isn't one I can make."
                )
            try:
                async with self._memory._write(guild_id, s.campaign_id, DM) as w:
                    refused = await self._store.lock_for_apply(w.conn, guild_id, s)
                    if refused == "":
                        return  # not `saving` any more: a repeat or another worker did it
                    if refused is not None:
                        raise MemoryRuleError(refused)
                    row = await w.get(ENTITIES, entity_id)  # (this campaign's rows only)
                    if row is None or row["status"] != CONFIRMED:
                        raise MemoryRuleError("That entry isn't there any more.")
                    rows = await w.select(
                        STATES, " AND entity_id = %s AND closed_at IS NULL", [entity_id]
                    )
                    current = str(rows[0]["condition"]) if rows else ""
                    was = data.get("was")
                    new = None  # (already so: nothing to write)
                    if current != condition:
                        if isinstance(was, str) and current != was:
                            # the DM changed it by hand after PlotBot looked: their word stands
                            raise MemoryRuleError("That entry changed since. Ask PlotBot again.")
                        new, _flags = await self._memory.set_condition_in(
                            w, entity_id, condition, at if type(at) is int else None
                        )
                    label = s.proposal.get("label")
                    label = label if isinstance(label, str) else ""
                    await self._store.mark_in(
                        w.conn, guild_id, s, "saved", extra={"state_id": new.id if new else None}
                    )
                    await self._store.answer_changed_in(
                        w.conn, guild_id, s, f"{'Saved' if new else 'Already so'}: {label}"
                    )
            except MemoryRuleError as rule:  # plain words, written for a DM
                return await self._finish(guild_id, s, "failed", str(rule))
        except Exception as exc:  # the type only: an error can quote a row
            log.warning("A saved plot proposal failed: %s", type(exc).__name__)
            try:
                await self._finish(guild_id, s, "failed", "That didn't work. Please try again.")
            except Exception:
                log.warning("A saved plot proposal could not be closed")

    async def _finish(self, guild_id: int, s: Saving, state: str, error: str = "") -> None:
        await self._store.mark(guild_id, s, state, error)

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
