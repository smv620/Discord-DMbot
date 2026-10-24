"""The connections a backstory reading found, waiting for the DM (#2380; docs/PLAN.md, "Names at
scale"). Per campaign, with the backstory's name suggestions as their lifetime: a connection waits
here until the DM keeps it, refuses it, or leaves the rest out; it goes when the campaign does, and
when either of its names is refused. It holds two entries' IDs, the relationship's key, one short
quote of the DM's own text and whether the text wrote it with a secret spelling. The text itself is
never kept. Nothing here is a fact: a kept connection is written by `MemoryStore.add_relation`
as the DM's word.

Every read cleans the queue, so a stale connection is never offered: one to a refused or missing
name is dropped, one to a merged name follows the merge, and one the campaign already has is
dropped.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database

MAX_WAITING = 300  # per campaign: a hostile text cannot grow the table
QUOTE_MAX = 400
MERGE_HOPS = 6  # a merge chain is followed this far; a longer one is dropped


@dataclass(frozen=True, slots=True)
class NewLink:
    subject_id: str
    predicate: str
    object_id: str
    quote: str
    secret: bool = False
    detail: str = ""  # the catch-all's own words ("pays"), else "" (#2454)
    truth: bool = False  # the DM said this upload is 100% truth (#2447): pinned when kept


@dataclass(frozen=True, slots=True)
class Waiting:
    subject_id: str
    predicate: str
    object_id: str
    quote: str
    secret: bool
    subject: str  # the names as they are now
    object: str
    ready: bool  # both are confirmed names: the DM may keep it now
    detail: str = ""  # the catch-all's own words ("pays"), else "" (#2454)
    truth: bool = False  # pin it as 100% truth when kept (#2447)
    id: str = ""  # the waiting row's own id, for the website's queue (#2635)


class BackstoryLinks:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    @staticmethod
    async def _lock(conn: Any, campaign_id: str) -> None:
        """One writer at a time for a campaign's queue (two presses, a press and a new reading)."""
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (campaign_id,))

    async def add(self, guild_id: int, campaign_id: str, links: Sequence[NewLink]) -> int:
        """Queue connections (a pair already waiting is kept as it is). Returns how many are new.
        At most MAX_WAITING wait for one campaign."""
        added = 0
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM backstory_links"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            room = max(0, MAX_WAITING - int(row["n"] if row else 0))
            for link in links:
                if added >= room or link.subject_id == link.object_id:
                    continue
                cur = await conn.execute(
                    "INSERT INTO backstory_links (guild_id, campaign_id, subject_id, predicate,"
                    " object_id, quote, secret, detail, truth, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, link.subject_id, link.predicate, link.object_id,
                     link.quote[:QUOTE_MAX], link.secret, link.detail, link.truth, now),
                )  # fmt: skip
                added += cur.rowcount
                if cur.rowcount == 0 and link.truth:  # already waiting: the new word is 100% truth
                    await conn.execute(
                        "UPDATE backstory_links SET truth = true WHERE guild_id = %s"
                        " AND campaign_id = %s AND subject_id = %s AND predicate = %s"
                        " AND object_id = %s",
                        (guild_id, campaign_id, link.subject_id, link.predicate, link.object_id),
                    )
        return added

    async def count(self, guild_id: int, campaign_id: str) -> int:
        """How many connections wait for this campaign, without cleaning the queue (the 📇 Names
        card shows it; the clean-up happens when they are asked)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM backstory_links"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def waiting(
        self, guild_id: int, campaign_id: str, *, symmetric: Collection[str] = ()
    ) -> list[Waiting]:
        """The connections still worth asking about, oldest first, after the clean-up above.
        `symmetric`: the relationships that read the same both ways (family, friend, enemy), so
        a pair the campaign has either way round is already known."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "SELECT id, subject_id, predicate, object_id, quote, secret, detail, truth,"
                " created_at"
                " FROM backstory_links"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY created_at, subject_id,"
                " predicate, object_id",
                (guild_id, campaign_id),
            )
            rows = list(await cur.fetchall())
            if not rows:
                return []
            entities = await self._entities(
                conn,
                guild_id,
                campaign_id,
                {r[k] for r in rows for k in ("subject_id", "object_id")},
            )
            known = await self._known(conn, guild_id, campaign_id, set(entities))
            out: list[Waiting] = []
            seen: set[tuple[str, str, str]] = set()
            drop: list[Any] = []  # rows to delete: the dead ones and the ones a merge moved
            move: list[tuple[str, str, str, Any]] = []  # where a moved row goes
            for r in rows:
                a, b = self._end(entities, r["subject_id"]), self._end(entities, r["object_id"])
                predicate = str(r["predicate"])
                key = (a or "", predicate, b or "")
                dead = (
                    a is None
                    or b is None
                    or a == b
                    or entities[a]["status"] == "rejected"
                    or entities[b]["status"] == "rejected"
                    or (a, predicate, b) in known
                    or (predicate in symmetric and (b, predicate, a) in known)
                    or key in seen
                )
                moved = (a, b) != (r["subject_id"], r["object_id"])
                if dead or moved:
                    drop.append(r)
                if dead:
                    continue
                assert a is not None and b is not None
                seen.add(key)
                if moved:  # it follows the merge: the same connection, on the name that stayed
                    move.append((a, predicate, b, r))
                ready = entities[a]["status"] == "confirmed" == entities[b]["status"]
                # A clue only some people know makes it secret. A name merely not shown to the
                # players yet (#2321; every new name starts so, #2404) does not: a backstory
                # connection is kept unrevealed instead, and cannot be shown before both names are.
                hidden = any(entities[x]["audience_limited"] for x in (a, b))
                out.append(
                    Waiting(
                        a, predicate, b, str(r["quote"]), bool(r["secret"]) or hidden,
                        str(entities[a]["name"]), str(entities[b]["name"]),
                        ready, str(r["detail"]), bool(r["truth"]), str(r["id"]),
                    )
                )  # fmt: skip
            # Deletes first, then inserts: a moved row may land on a key a dropped row had.
            for r in drop:
                await self._drop(conn, guild_id, campaign_id, r)
            for a, predicate, b, r in move:
                await conn.execute(
                    "INSERT INTO backstory_links (guild_id, campaign_id, subject_id,"
                    " predicate, object_id, quote, secret, detail, truth, created_at, id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, a, predicate, b, r["quote"], r["secret"],
                     r["detail"], r["truth"], r["created_at"], r["id"]),
                )  # fmt: skip
        return out

    async def remove(self, guild_id: int, campaign_id: str, link: Waiting) -> None:
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            await self._drop(
                conn,
                guild_id,
                campaign_id,
                {"subject_id": link.subject_id, "predicate": link.predicate,
                 "object_id": link.object_id},
            )  # fmt: skip

    async def remove_all(self, guild_id: int, campaign_id: str, links: Sequence[Waiting]) -> None:
        for link in links:
            await self.remove(guild_id, campaign_id, link)

    @staticmethod
    async def _drop(conn: Any, guild_id: int, campaign_id: str, row: Any) -> None:
        await conn.execute(
            "DELETE FROM backstory_links WHERE guild_id = %s AND campaign_id = %s"
            " AND subject_id = %s AND predicate = %s AND object_id = %s",
            (guild_id, campaign_id, row["subject_id"], row["predicate"], row["object_id"]),
        )

    @staticmethod
    async def _entities(
        conn: Any, guild_id: int, campaign_id: str, ids: set[str]
    ) -> dict[str, dict[str, Any]]:
        """The entries these IDs stand for, and the ones merges point to (a few hops)."""
        found: dict[str, dict[str, Any]] = {}
        want = set(ids)
        for _ in range(MERGE_HOPS):
            want -= set(found)
            if not want:
                break
            cur = await conn.execute(
                "SELECT id, name, status, merged_into, hidden_from_players, audience_limited"
                " FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)",
                (guild_id, campaign_id, list(want)),
            )
            for r in await cur.fetchall():
                found[str(r["id"])] = r
                if r["merged_into"]:
                    want.add(str(r["merged_into"]))
        return found

    @staticmethod
    def _end(entities: dict[str, dict[str, Any]], entity_id: str) -> str | None:
        """The entry that stands for this one now: itself, or where its merge led."""
        for _ in range(MERGE_HOPS):
            row = entities.get(entity_id)
            if row is None:
                return None
            if row["status"] != "merged":
                return entity_id
            entity_id = str(row["merged_into"])
        return None

    @staticmethod
    async def _known(
        conn: Any, guild_id: int, campaign_id: str, ids: set[str]
    ) -> set[tuple[str, str, str]]:
        cur = await conn.execute(
            "SELECT subject_id, predicate, object_id FROM memory_relations"
            " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
            " AND subject_id = ANY(%s) AND object_id = ANY(%s)",
            (guild_id, campaign_id, list(ids), list(ids)),
        )
        return {
            (str(r["subject_id"]), str(r["predicate"]), str(r["object_id"]))
            for r in await cur.fetchall()
        }
