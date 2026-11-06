"""The recap texts a DM approved, kept as each session's summary in the campaign notes (#2122).

When the DM approves a recap's text (the website's Approve or the Discord button), the database
writes that text here as the session's next version: never overwritten, and not repeated when the
same text is approved again. A session keeps version 1 and the 19 newest. PlotBot, the pre-session
note, recall and the later plot checks read the newest version of each session
(`AcceptedStore.newest`). Words only, per campaign and isolated by row-level security; it goes in
the campaign's backup (`AcceptedSection`) and with the campaign when it is deleted. Nothing here is
shown to players or on a shared link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.recaps.approval import text_hash

VERSIONS_PER_SESSION = 20  # a session keeps this many: version 1 and the 19 newest (0209)
INT32_MAX = 2**31 - 1  # version numbers only go up, so the number itself is not capped at 20
MAX_VERSIONS = 5000  # in one campaign's backup: 250 sessions at the most each can hold
INT64_MAX = 2**63 - 1
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)


@dataclass(frozen=True, slots=True)
class Accepted:
    session_at: int
    version: int
    text_hash: str
    title: str
    summary: str
    accepted_by: int
    accepted_at: int


def _accepted(row: Any) -> Accepted:
    return Accepted(
        int(row["session_at"]),
        int(row["version"]),
        str(row["text_hash"]),
        str(row["title"]),
        str(row["summary"]),
        int(row["accepted_by"]),
        int(row["accepted_at"]),
    )


_COLUMNS = "session_at, version, text_hash, title, summary, accepted_by, accepted_at"


class AcceptedStore:
    """Reads of the accepted versions. The writing is the approval door's (migration 0209)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def newest(
        self, guild_id: int, campaign_id: str, *, before: int | None = None, last: int | None = None
    ) -> list[Accepted]:
        """The newest accepted version of each session, oldest session first. `before` keeps only
        sessions earlier than that one and `last` only the latest that many of them, so a long
        campaign is not read whole for a few recaps (#2954)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT * FROM (SELECT DISTINCT ON (session_at) {_COLUMNS} FROM recap_accepted"
                " WHERE guild_id = %s AND campaign_id = %s AND (%s::bigint IS NULL OR"
                " session_at < %s) ORDER BY session_at, version DESC) AS newest"
                " ORDER BY session_at DESC LIMIT %s",
                (guild_id, campaign_id, before, before, last),
            )
            return [_accepted(r) for r in reversed(await cur.fetchall())]

    async def versions(self, guild_id: int, campaign_id: str, session_at: int) -> list[Accepted]:
        """Every accepted version of one session, oldest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM recap_accepted WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s ORDER BY version",
                (guild_id, campaign_id, session_at),
            )
            return [_accepted(r) for r in await cur.fetchall()]


class AcceptedSection:
    """The accepted versions in campaign backups (an `ExportSection`)."""

    name = "recap_accepted"
    _KEYS = frozenset(
        {"session_at", "version", "text_hash", "title", "summary", "accepted_by", "accepted_at"}
    )

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM recap_accepted WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY session_at, version",
            (guild_id, campaign_id),
        )
        return [
            {
                "session_at": a.session_at,
                "version": a.version,
                "text_hash": a.text_hash,
                "title": a.title,
                "summary": a.summary,
                "accepted_by": str(a.accepted_by),
                "accepted_at": a.accepted_at,
            }
            for a in map(_accepted, await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[Accepted]:
        if not isinstance(rows, list) or len(rows) > MAX_VERSIONS:
            raise CampaignError(DAMAGED)
        out: list[Accepted] = []
        seen: set[tuple[int, int]] = set()
        per_session: dict[int, int] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != self._KEYS:
                raise CampaignError(DAMAGED)
            at, version, stamp = row["session_at"], row["version"], row["accepted_at"]
            by, digest = row["accepted_by"], row["text_hash"]
            if (
                any(type(n) is not int for n in (at, version, stamp))
                or not 0 < at <= INT64_MAX
                or not 0 <= stamp <= INT64_MAX
                or (at, version) in seen
                or not (isinstance(by, str) and by.isascii() and by.isdigit())
                or not 0 < int(by) <= INT64_MAX
                or not 1 <= version <= INT32_MAX
            ):
                raise CampaignError(DAMAGED)
            for key, limit in (("title", 120), ("summary", 6000)):
                value = row[key]
                if not isinstance(value, str) or not 1 <= len(value) <= limit:
                    raise CampaignError(DAMAGED)
            # The door decides "a new version" by this hash, so it must be the text's own.
            if digest != text_hash(row["title"], row["summary"]):
                raise CampaignError(DAMAGED)
            seen.add((at, version))
            per_session[at] = per_session.get(at, 0) + 1
            if per_session[at] > VERSIONS_PER_SESSION:
                raise CampaignError(DAMAGED)
            out.append(Accepted(at, version, digest, row["title"], row["summary"], int(by), stamp))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        # The store hands over what `check` returned (it checks in a worker thread before the
        # transaction opens); a raw list is checked here.
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, Accepted) for r in rows)
            else self.check(rows or [])
        )
        for a in checked:
            await conn.execute(
                "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                " text_hash, title, summary, accepted_by, accepted_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    guild_id,
                    campaign_id,
                    a.session_at,
                    a.version,
                    a.text_hash,
                    a.title,
                    a.summary,
                    a.accepted_by,
                    a.accepted_at,
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_accepted WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
