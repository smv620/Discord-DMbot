"""Where the beings the party met in play are kept until the DM names one (#3172 slice 5;
Supervisor's ruling, option C, 2026-11-10): the table `memory_met`, apart from the campaign's
names, so nothing that lists names can show them (quiet by construction).

A row is one encounter in one session: the DM's word for them, how many, the line numbers it came
from and who spoke those lines. Rows are transcript-derived, so they go with their session (the
foreign key), with its transcript (`forget_session`) and with anyone they cite (`forget_speaker`).
Nothing in the bot reads them yet but the tests: the DM's list is #4045, and naming or joining one
there writes the entry at rank 0 (`importance_rank.MET_IN_PLAY`) and deletes the row in one
transaction. Counts only leave this module; its words are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.models import name_key, new_id

ROWS_MAX = 100  # per session: the reader's cap is 10, so this is only a guard


@dataclass
class MetAdded:
    """What `MetStore.add` did, as counts only."""

    made: int = 0
    again: int = 0  # already kept for this session (a later batch, or a restarted read)
    named: int = 0  # the words are a name the campaign already has: not an unnamed being


@dataclass(frozen=True)
class MetRow:
    id: str
    session_id: str
    session_started_at: int | None
    what: str
    count: int
    lines: tuple[str, ...]
    speaker_ids: tuple[int, ...]


def what_key(what: str) -> str:
    """The same words twice in a session are one encounter: the unnamed person's own key (case,
    an article and punctuation ignored), so "the barbarians" finds "barbarians". Pure."""
    return unnamed_mod.same_person_key(what, None)


class MetStore:
    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        met: Sequence[tuple[str, int, Sequence[str], Sequence[int]]],
        *,
        session_started_at: int | None,
    ) -> MetAdded:
        """Keep these encounters of one session, each as (the DM's word for them, how many, the
        line numbers, who spoke those lines). Words that are a name the campaign already has are no
        unnamed being (`named`); the same words already kept for this session are `again` (the
        unique index holds it even when two reads overlap). Across sessions the same words are a
        new row: two wolves a month apart are often two wolves."""
        out = MetAdded()
        if not met:
            return out
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status IN ('proposed', 'confirmed') AND NOT unnamed",
                (guild_id, campaign_id),
            )
            named = {name_key(str(r["name"])) for r in await cur.fetchall()}
            cur = await conn.execute(
                "SELECT key FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                " AND status IN ('proposed', 'confirmed')",
                (guild_id, campaign_id),
            )
            named |= {str(r["key"]) for r in await cur.fetchall()}
            for word, count, lines, speakers in met:
                what = unnamed_mod.tidy(word)
                key = what_key(what)
                if not what or {name_key(what), key.removeprefix("|")} & named:
                    out.named += 1
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_met (guild_id, campaign_id, id, session_id,"
                    " session_started_at, what, what_key, count, lines, speaker_ids, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, session_id, what_key) DO NOTHING",
                    (
                        guild_id, campaign_id, new_id(), session_id, session_started_at, what,
                        key, count, [str(n) for n in lines][:10], sorted(set(speakers)), now,
                    ),
                )  # fmt: skip
                if cur.rowcount:
                    out.made += 1
                else:
                    out.again += 1
        return out

    async def of_session(self, guild_id: int, campaign_id: str, session_id: str) -> list[MetRow]:
        """This session's rows, oldest first (for the tests now, and #4045's list later)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, session_id, session_started_at, what, count, lines, speaker_ids"
                " FROM memory_met WHERE guild_id = %s AND campaign_id = %s AND session_id = %s"
                " ORDER BY created_at, id LIMIT %s",
                (guild_id, campaign_id, session_id, ROWS_MAX),
            )
            return [_row(r) for r in await cur.fetchall()]


def _row(r: Any) -> MetRow:
    return MetRow(
        str(r["id"]), str(r["session_id"]),
        None if r["session_started_at"] is None else int(r["session_started_at"]),
        str(r["what"]), int(r["count"]), tuple(r["lines"]), tuple(int(u) for u in r["speaker_ids"]),
    )  # fmt: skip


async def forget_session(conn: Any, guild_id: int, session_id: str) -> None:
    """A session's transcript is deleted: its met rows go with it (they hold the DM's words and
    line numbers). In the caller's transaction."""
    await conn.execute(
        "DELETE FROM memory_met WHERE guild_id = %s AND session_id = %s", (guild_id, session_id)
    )


async def forget_speaker(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> None:
    """Someone's lines are deleted (they stopped being recorded, or the DM removed them): every met
    row that cites them goes. `campaign_id`: only that campaign's rows. In the caller's
    transaction."""
    await conn.execute(
        "DELETE FROM memory_met WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )


def speakers_of(lines: Collection[str], everyone: Mapping[str, int | None]) -> list[int]:
    """Who spoke these lines, as the extractor knows them. Pure."""
    return sorted({u for n in lines if (u := everyone.get(n)) is not None})
