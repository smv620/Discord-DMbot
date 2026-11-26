"""The bot's side of posting a graph improvement report (#3742, slice B): the stored reports
(`graph_reviews`) that were never posted are taken one at a time, built into a Discussion under the
posting rules checked *now* (`graph_review_post.render`), and posted to the owner's private
repository, at most one a round and one a minute.

- With no repository or token set the poster is not built, so nothing is posted and nothing waits.
- A row is taken with a lease (`posting_at`): ten minutes while it posts. A report that could not go
  (held back, or a failed try) is looked at again an hour later; after five failed tries the poster
  stops trying that report, and it stays on the server.
- A post that went out but was never marked (a crash between the two) is found by its marker among
  the category's latest discussions and marked, never posted twice.
- Counts and reason words only in the log; the token is never here (the transport holds it).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection
from typing import Any

import aiohttp

from dmbot.db import Database
from dmbot.memory import graph_review_post as gp

log = logging.getLogger(__name__)

LEASE_S = 600  # a report being posted is not taken again for this long
RETRY_S = 3600  # a held report or a failed try is looked at again after this long
MAX_FAILURES = 5
API = "https://api.github.com/graphql"
CALL_TIMEOUT_S = 20

Allowed = Callable[[Collection[int]], Awaitable[set[int]]]


def aiohttp_transport(token: str, session: aiohttp.ClientSession) -> gp.Transport:
    """GitHub's GraphQL over HTTPS. The token is only in the `Authorization` header, never in a
    message; a failure says its kind (`http_401`, `graphql_error`), never GitHub's own words, which
    can quote what was sent."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async def call(query: str, variables: Any) -> dict[str, Any]:
        try:
            async with session.post(
                API,
                json={"query": query, "variables": dict(variables)},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=CALL_TIMEOUT_S),
            ) as reply:
                if reply.status != 200:
                    raise gp.PostError(f"http_{reply.status}")
                body = await reply.json()
        except aiohttp.ClientError as exc:
            raise gp.PostError(f"network_{type(exc).__name__}"[:40]) from None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(body, dict) or body.get("errors") or not isinstance(data, dict):
            raise gp.PostError("graphql_error")
        return dict(data)

    return call


class GraphReportStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> tuple[gp.Stored, bool] | None:
        """The oldest report not posted that is due, taken with a lease, and whether its campaign
        shares its story to improve DMbot right now. A report from before verdicts were kept
        (`verdicts` empty) is never taken."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT g.guild_id, g.campaign_id, g.id FROM graph_reviews g"
                " WHERE g.guild_id = %s AND g.posted_at IS NULL AND g.verdicts IS NOT NULL"
                " AND g.post_failures < %s AND (g.posting_at IS NULL OR g.posting_at < %s -"
                "  CASE WHEN g.post_failures > 0 OR g.held <> '' THEN %s ELSE %s END)"
                " ORDER BY g.created_at, g.id LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE graph_reviews r SET posting_at = %s FROM pick"
                " WHERE r.guild_id = pick.guild_id AND r.campaign_id = pick.campaign_id"
                " AND r.id = pick.id"
                " RETURNING r.id, r.campaign_id, r.created_at, r.review_commit, r.checked,"
                " r.missed, r.speaker_ids, r.verdicts,"
                " (SELECT c.share_improve FROM campaigns c WHERE c.guild_id = r.guild_id"
                "  AND c.id = r.campaign_id) AS sharing",
                (guild_id, MAX_FAILURES, now, RETRY_S, LEASE_S, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        report = gp.Stored(
            str(row["id"]),
            str(row["campaign_id"]),
            int(row["created_at"]),
            row["review_commit"],
            int(row["checked"]),
            int(row["missed"]),
            tuple(int(s) for s in row["speaker_ids"]),
            dict(row["verdicts"]),
        )
        return report, bool(row["sharing"])

    async def posted(self, guild_id: int, report: gp.Stored, number: int) -> None:
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE graph_reviews SET posted_at = %s, discussion = %s, held = '',"
                " post_failures = 0 WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (now, number, guild_id, report.campaign_id, report.id),
            )

    async def held(self, guild_id: int, report: gp.Stored, why: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE graph_reviews SET held = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (why[:30], guild_id, report.campaign_id, report.id),
            )

    async def failed(self, guild_id: int, report: gp.Stored, *, its_fault: bool) -> None:
        """A try that did not post. Only a report's own fault counts toward giving up on it: a
        token or a category that is wrong, or GitHub being down, would otherwise end every report
        after five hours and fixing the cause would not bring them back. The row stays leased, so it
        is tried again in an hour either way."""
        if not its_fault:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE graph_reviews SET post_failures = post_failures + 1"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, report.campaign_id, report.id),
            )


class ReportPoster:
    def __init__(
        self,
        store: GraphReportStore,
        github: gp.GitHub,
        *,
        consenting: Callable[[int], Awaitable[Collection[int]]],
        improvement_allowed: Allowed,
    ) -> None:
        self._store = store
        self._github = github
        self._consenting = consenting
        self._allowed = improvement_allowed

    async def _people(self, guild_id: int) -> set[int]:
        """Who may be quoted now: agrees to be recorded, and has not opted out of improving
        DMbot (the same question the check itself asks, `GraphReviews._people`)."""
        return set(await self._allowed(set(await self._consenting(guild_id))))

    async def round(self, guild_id: int) -> int:
        """Post at most one report of this server. A report that can't go is held or counted as a
        failed try and the next one is looked at; the round ends after the first post. Returns how
        many were posted (0 or 1)."""
        while (taken := await self._store.claim_next(guild_id)) is not None:
            report, sharing = taken
            try:
                made = gp.render(
                    report, allowed_speakers=await self._people(guild_id), sharing=sharing
                )
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                # a stored report that can't be read: held with a fixed word, never retried hot
                made = gp.Held(gp.BAD_VERDICTS)
                log.warning("A graph report could not be read: %s", type(exc).__name__)
            if isinstance(made, gp.Held):
                await self._store.held(guild_id, report, made.why)
                log.info("A graph report was held back: %s", made.why)
                continue
            try:
                number = await self._github.already(report.id)
                if number is None:
                    number = await self._github.create(made)
            except gp.PostError as exc:
                await self._store.failed(guild_id, report, its_fault=str(exc) == "not_created")
                log.warning("A graph report could not be posted: %s", exc)
                return 0
            except Exception as exc:  # the type only: nothing here may quote the report
                await self._store.failed(guild_id, report, its_fault=False)
                log.warning("A graph report could not be posted: %s", type(exc).__name__)
                return 0
            await self._store.posted(guild_id, report, number)
            log.info("A graph report was posted (cut out: %s)", dict(made.cut))
            return 1
        return 0
