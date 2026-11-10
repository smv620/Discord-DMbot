"""Where the beings the party met in play are kept until the DM names one (#3172 slice 5;
Supervisor's ruling, option C, 2026-11-10): the table `memory_met`, apart from the campaign's
names, so nothing that lists names can show them (quiet by construction).

A row is one encounter in one session: the DM's word for them, how many, when each line it came
from began, and who spoke those lines. Rows are transcript-derived, so they go with their session
(the foreign key), with its transcript (`forget_session`) and with anyone they cite
(`forget_speaker`, on every path that forgets a person's words). Nothing in the bot reads them yet
but the tests: the DM's list is #4045, and naming or joining one there writes the entry at rank 0
(`importance_rank.MET_IN_PLAY`) and deletes the row in one transaction. Counts only leave this
module; its words are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.models import name_key, new_id

MAX_PER_SESSION = 10  # Supervisor, 2026-11-10: the sample session met 4 (11 if counted one by one)
_ENDINGS = (("ies", "y"), ("ves", "f"), ("s", ""))  # (only enough for a key: never shown)


def _singular(word: str) -> str:
    for end, put in _ENDINGS:
        if word.endswith(end) and not word.endswith("ss") and len(word) > len(end) + 2:
            return word[: -len(end)] + put
    return word


def what_key(what: str) -> str:
    """Two items with this key are one encounter of a session: case, an article, punctuation and a
    plain plural ignored, so "the barbarians" is "barbarian" and "wolves" is "wolf". Pure."""
    plain = unnamed_mod.same_person_key(what, None).removeprefix("|")
    return " ".join(_singular(w) for w in plain.split())


@dataclass
class MetAdded:
    """What `MetStore.add` did, as counts only."""

    made: int = 0
    again: int = 0  # already kept for this session (a later batch, or a restarted read)
    named: int = 0  # the words are a name the campaign already has: not an unnamed being
    past_cap: int = 0  # the session already has `MAX_PER_SESSION`: not kept


@dataclass(frozen=True)
class MetRow:
    id: str
    session_id: str
    session_started_at: int | None
    what: str
    count: int
    line_ms: tuple[int, ...]
    speaker_ids: tuple[int, ...]


class MetStore:
    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        met: Sequence[tuple[str, int, Sequence[int], Sequence[int]]],
        *,
        session_started_at: int | None,
    ) -> MetAdded:
        """Keep these encounters of one session, each as (the DM's word for them, how many, when
        each of its lines began, who spoke those lines). Words that are a name the campaign already
        has are no unnamed being (`named`); the same words already kept for this session are
        `again` (the unique index holds it even when two reads overlap); past `MAX_PER_SESSION`
        rows for the session, across every read of it, nothing is kept (`past_cap`). The session's
        row is locked first, so two reads of one session count the cap one after the other.
        Across sessions the same words are a new row: two wolves a month apart are often two
        wolves."""
        out = MetAdded()
        if not met:
            return out
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s FOR UPDATE",
                (guild_id, campaign_id, session_id),
            )
            if await cur.fetchone() is None:
                return out  # (the session is gone, or not this campaign's)
            cur = await conn.execute(
                "SELECT what_key FROM memory_met WHERE guild_id = %s AND campaign_id = %s"
                " AND session_id = %s",
                (guild_id, campaign_id, session_id),
            )
            kept = {str(r["what_key"]) for r in await cur.fetchall()}
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
            for word, count, line_ms, speakers in met:
                what = unnamed_mod.tidy(word)
                plain = unnamed_mod.same_person_key(what, None).removeprefix("|")
                if not what or {name_key(what), plain} & named:
                    out.named += 1
                    continue
                key = what_key(what)
                if key in kept:
                    out.again += 1
                    continue
                if len(kept) >= MAX_PER_SESSION:
                    out.past_cap += 1
                    continue
                await conn.execute(
                    "INSERT INTO memory_met (guild_id, campaign_id, id, session_id,"
                    " session_started_at, what, what_key, count, line_ms, speaker_ids, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, campaign_id, new_id(), session_id, session_started_at, what,
                        key, count, sorted(set(line_ms))[:10], sorted(set(speakers)), now,
                    ),
                )  # fmt: skip
                kept.add(key)
                out.made += 1
        return out

    async def of_session(self, guild_id: int, campaign_id: str, session_id: str) -> list[MetRow]:
        """This session's rows, oldest first (for the tests now; #4045's list must also leave out
        rows citing someone no longer recorded, as the deed and reveal readers do)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, session_id, session_started_at, what, count, line_ms, speaker_ids"
                " FROM memory_met WHERE guild_id = %s AND campaign_id = %s AND session_id = %s"
                " ORDER BY created_at, id",
                (guild_id, campaign_id, session_id),
            )
            return [_row(r) for r in await cur.fetchall()]


def _row(r: Any) -> MetRow:
    return MetRow(
        str(r["id"]), str(r["session_id"]),
        None if r["session_started_at"] is None else int(r["session_started_at"]),
        str(r["what"]), int(r["count"]), tuple(int(n) for n in r["line_ms"]),
        tuple(int(u) for u in r["speaker_ids"]),
    )  # fmt: skip


async def forget_session(conn: Any, guild_id: int, session_id: str) -> None:
    """A session's transcript is deleted: its met rows go with it (they hold the DM's words and
    point at its lines). In the caller's transaction."""
    await conn.execute(
        "DELETE FROM memory_met WHERE guild_id = %s AND session_id = %s", (guild_id, session_id)
    )


async def forget_speaker(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Someone's words are forgotten ("Stop recording me", "Delete my transcripts", a DM's removal
    of a speaker): every met row that cites them goes. `campaign_id`: only that campaign's rows.
    In the caller's transaction; returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_met WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    return int(cur.rowcount or 0)


def speakers_of(lines: Collection[str], everyone: Mapping[str, int | None]) -> list[int]:
    """Who spoke these lines, as the extractor knows them. Pure."""
    return sorted({u for n in lines if (u := everyone.get(n)) is not None})
