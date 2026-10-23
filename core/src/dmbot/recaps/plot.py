"""Running PlotBot's check of ready recaps (#2122): which recaps wait for one, the one careful-tier
call, and the findings written for the DM's recap page (`web/plot.py` reads them).

A check is for one text of a recap (its hash). It starts when a recap is ready and its text has no
check yet, and starts again when the text changes. At most `PER_HOUR` checks begin for a campaign
in an hour, and a check that was started but never finished is tried again `TRIES` times. A recap
with no earlier accepted recap has nothing to be compared with: it is marked checked with no call.
An issue the DM already answered "Not a problem" (the same point in the same words) is not raised
again. The findings are advice for the DM alone: nothing here is posted, logged or sent anywhere.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from dmbot.db import Database
from dmbot.memory import plot_check as pc
from dmbot.recaps.accepted import AcceptedStore
from dmbot.recaps.draft import sentences_of

log = logging.getLogger(__name__)

PER_HOUR = 6  # checks that may begin for one campaign in an hour
TRIES = 3
STALE_S = 600  # a started check not finished in this long is tried again
PER_ROUND = 3  # checks run in one round, across all servers
_HASH = "encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
_JOB_HASH = "encode(sha256(convert_to(j.title || E'\\n' || j.summary, 'UTF8')), 'hex')"


class Completer(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Any: ...


class PlotStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def waiting(self, guild_id: int, *, limit: int = 5) -> list[tuple[str, int, str]]:
        """(campaign, session, text hash) of ready drafts whose text has no check, or whose check
        began long ago and never finished, within each campaign's hourly allowance."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT j.campaign_id, j.session_at, {_JOB_HASH} AS h"
                " FROM recap_jobs j LEFT JOIN plot_checks k ON k.guild_id = j.guild_id"
                " AND k.campaign_id = j.campaign_id AND k.session_at = j.session_at"
                f" AND k.text_hash = {_JOB_HASH}"
                " WHERE j.guild_id = %s AND j.state = 'ready' AND j.status = 'draft'"
                " AND (k.text_hash IS NULL OR (k.state = 'running' AND k.created_at < %s"
                "   AND k.tries < %s))"
                " AND (SELECT count(*) FROM plot_checks r WHERE r.guild_id = j.guild_id"
                "   AND r.campaign_id = j.campaign_id AND r.created_at > %s) < %s"
                " ORDER BY j.session_at LIMIT %s",
                (guild_id, now - STALE_S, TRIES, now - 3600, PER_HOUR, limit),
            )
            return [
                (r["campaign_id"], int(r["session_at"]), str(r["h"])) for r in await cur.fetchall()
            ]

    async def begin(self, guild_id: int, campaign_id: str, session_at: int, digest: str) -> bool:
        """Start (or start again) the check of this text. False: someone else has it."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO plot_checks (guild_id, campaign_id, session_at, text_hash, state,"
                " tries, created_at) VALUES (%s, %s, %s, %s, 'running', 1, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at, text_hash) DO UPDATE"
                " SET tries = plot_checks.tries + 1, created_at = EXCLUDED.created_at"
                " WHERE plot_checks.state = 'running' AND plot_checks.created_at < %s"
                " AND plot_checks.tries < %s",
                (guild_id, campaign_id, session_at, digest, now, now - STALE_S, TRIES),
            )
            return bool(cur.rowcount)

    async def text(
        self, guild_id: int, campaign_id: str, session_at: int
    ) -> tuple[str, str] | None:
        """(title, summary) of the ready draft now, or None."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT title, summary FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND state = 'ready' AND status = 'draft'",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            return (row["title"], row["summary"]) if row else None

    async def finish(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        digest: str,
        findings: list[pc.Finding],
        calls: int,
    ) -> int:
        """Write the findings and mark the check done, in one step, but only if the recap's text is
        still the one that was checked (an edit meanwhile makes the answer about old words).
        Returns how many issues were written."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT 1 FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                f" AND session_at = %s AND {_HASH} = %s",
                (guild_id, campaign_id, session_at, digest),
            )
            if await cur.fetchone() is None:
                await conn.execute(
                    "DELETE FROM plot_checks WHERE guild_id = %s AND campaign_id = %s"
                    " AND session_at = %s AND text_hash = %s",
                    (guild_id, campaign_id, session_at, digest),
                )
                return 0
            cur = await conn.execute(
                "SELECT fine_key FROM plot_fine WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            fine = {r["fine_key"] for r in await cur.fetchall()}
            written = 0
            for f in findings:
                if f.fine_key in fine:
                    continue  # the DM said "Not a problem" to this same point
                await conn.execute(
                    "INSERT INTO plot_issues (guild_id, campaign_id, id, session_at, text_hash,"
                    " kind, level, sentence, sentence_text, reason, source_text, source_session,"
                    " fine_key, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s)",
                    (
                        guild_id, campaign_id, uuid.uuid4().hex, session_at, digest, f.kind,
                        f.level, f.sentence, f.sentence_text, f.reason, f.source_text,
                        f.source_title, f.fine_key, now,
                    ),
                )  # fmt: skip
                written += 1
            await conn.execute(
                "UPDATE plot_checks SET state = 'done', calls = %s, finished_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s AND text_hash = %s",
                (calls, now, guild_id, campaign_id, session_at, digest),
            )
            return written

    async def fail(self, guild_id: int, campaign_id: str, session_at: int, digest: str) -> None:
        """Give this text's check up: the page reads 'failed' and Approve is never held for it."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE plot_checks SET state = 'failed', finished_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND text_hash = %s",
                (int(self._now()), guild_id, campaign_id, session_at, digest),
            )


class PlotRunner:
    def __init__(self, store: PlotStore, accepted: AcceptedStore, ai: Completer | None) -> None:
        self._store = store
        self._accepted = accepted
        self._ai = ai

    async def round(self, guild_id: int, *, limit: int = PER_ROUND) -> int:
        """Run the checks that wait in one server, at most `limit`. Returns how many ran."""
        ran = 0
        for campaign_id, session_at, digest in await self._store.waiting(guild_id, limit=limit):
            if await self._store.begin(guild_id, campaign_id, session_at, digest):
                ran += 1
                await self._check(guild_id, campaign_id, session_at, digest)
        return ran

    async def _check(self, guild_id: int, campaign_id: str, session_at: int, digest: str) -> None:
        try:
            now = await self._store.text(guild_id, campaign_id, session_at)
            if now is None:
                return await self._store.fail(guild_id, campaign_id, session_at, digest)
            sentences = sentences_of(now[1])
            earlier = [
                pc.Earlier(a.session_at, a.title, a.summary)
                for a in reversed(await self._accepted.newest(guild_id, campaign_id))
                if a.session_at != session_at
            ]
            if not sentences or not earlier or self._ai is None:
                # Nothing to compare with (or no AI): checked, nothing found; no call is made.
                found: list[pc.Finding] = []
                calls = 0
            else:
                reply = await self._ai.complete(
                    pc.system(),
                    pc.message(sentences, earlier),
                    max_tokens=pc.TOKENS,
                )
                calls = 1
                if getattr(reply, "refused", False) or getattr(reply, "cut", False):
                    return await self._store.fail(guild_id, campaign_id, session_at, digest)
                parsed = pc.parse(reply.text, sentences, earlier)
                if parsed is None:
                    return await self._store.fail(guild_id, campaign_id, session_at, digest)
                found = parsed
            await self._store.finish(guild_id, campaign_id, session_at, digest, found, calls)
        except Exception as exc:  # the type only: an AI or database error can quote a row
            log.warning("The plot check failed: %s", type(exc).__name__)
            try:
                await self._store.fail(guild_id, campaign_id, session_at, digest)
            except Exception:
                log.warning("The plot check could not be marked failed")
