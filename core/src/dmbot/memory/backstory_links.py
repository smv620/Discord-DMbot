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
from dmbot.memory.pronouns import is_pronoun

MAX_WAITING = 300  # per campaign: a hostile text cannot grow the table
MAX_WHICH = 50  # "Which Mara?" questions waiting per campaign
MAX_CANDIDATES = 10
# What `pick_which` says: the answer was taken, the question is gone (or the pick was not one of
# its candidates), or too many connections wait already, so the question stays.
DONE, GONE, FULL = "done", "gone", "full"
QUOTE_MAX = 400
UPLOAD_MAX = 120  # the upload's name and date (the column's limit)
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
    upload: str = ""  # the upload's name and date, as the DM saw them (#2650)


@dataclass(frozen=True, slots=True)
class NewWhich:
    """A connection whose one end fits several names (#2650): `other_id` is the end that is a
    name (the connection's subject when `other_is_subject`), `word` the other end as written."""

    other_id: str
    other_is_subject: bool
    predicate: str
    word: str
    candidates: tuple[str, ...]  # entry ids, at least two
    quote: str
    secret: bool = False
    detail: str = ""
    truth: bool = False
    upload: str = ""


@dataclass(frozen=True, slots=True)
class WhichWaiting:
    id: str
    other_id: str
    other: str  # the name, as it is now
    other_is_subject: bool
    predicate: str
    word: str
    candidates: tuple[tuple[str, str], ...]  # (entry id, name), still live
    quote: str
    detail: str
    upload: str


@dataclass(frozen=True, slots=True)
class Taken:
    """What `take_ready_notices` marked told: a count for each upload, and the rows' ids (to
    un-mark them if the notice could not be posted)."""

    counts: dict[str, int]
    ids: list[str]


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
    upload: str = ""  # the upload it came from ("" for one queued before #2650)
    told: bool = False  # the DM screen has said it is ready (#2650)


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
                    " object_id, quote, secret, detail, truth, created_at, upload)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, link.subject_id, link.predicate, link.object_id,
                     link.quote[:QUOTE_MAX], link.secret, link.detail, link.truth, now,
                     link.upload[:UPLOAD_MAX]),
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
        """How many connections wait for this campaign, with the "Which Mara?" questions, without
        cleaning the queue (the 📇 Names card shows it; the clean-up happens when they are
        asked)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM backstory_links WHERE guild_id = %s"
                " AND campaign_id = %s) + (SELECT count(*) FROM backstory_which WHERE guild_id = %s"
                " AND campaign_id = %s) AS n",
                (guild_id, campaign_id, guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def count_which(self, guild_id: int, campaign_id: str) -> int:
        """How many "Which one?" questions wait (read-only)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM backstory_which"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def add_which(self, guild_id: int, campaign_id: str, rows: Sequence[NewWhich]) -> int:
        """Queue "Which one?" questions (one already waiting for the same end and word is kept as
        it is). Returns how many are new; at most MAX_WHICH wait for a campaign."""
        added = 0
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM backstory_which"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            room = max(0, MAX_WHICH - int(row["n"] if row else 0))
            for w in rows:
                if added >= room or len(set(w.candidates)) < (1 if is_pronoun(w.word) else 2):
                    continue
                candidates = list(dict.fromkeys(w.candidates))[:MAX_CANDIDATES]
                if len(candidates) < 2:
                    # A pronoun's question with one name ("Mara, or someone else", #2790): the table
                    # wants two, so the question's own named end fills the second place. It is never
                    # offered (a candidate that is the other end is skipped when the question is
                    # read), so the DM sees one name and "Someone else".
                    candidates.append(w.other_id)
                cur = await conn.execute(
                    "INSERT INTO backstory_which (guild_id, campaign_id, other_id,"
                    " other_is_subject, predicate, word, candidates, quote, secret, detail, truth,"
                    " upload, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s) ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, w.other_id, w.other_is_subject, w.predicate,
                     w.word[:80], candidates,
                     w.quote[:QUOTE_MAX], w.secret, w.detail, w.truth, w.upload[:UPLOAD_MAX], now),
                )  # fmt: skip
                added += cur.rowcount
        return added

    # (A question's candidates are cleaned lazily: a refused or merged name is dropped or followed
    # when the question is next read, not when the name changes.)
    async def which_waiting(self, guild_id: int, campaign_id: str) -> list[WhichWaiting]:
        """The questions still worth asking, oldest first. A candidate that was refused or merged
        away is dropped (or follows the merge); a question left with fewer than two candidates,
        or about a name that is gone or refused, is not asked and is deleted."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "SELECT id, other_id, other_is_subject, predicate, word, candidates, quote, detail,"
                " upload FROM backstory_which WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY created_at, id",
                (guild_id, campaign_id),
            )
            rows = list(await cur.fetchall())
            if not rows:
                return []
            wanted = {r["other_id"] for r in rows} | {c for r in rows for c in r["candidates"]}
            entities = await self._entities(conn, guild_id, campaign_id, wanted)
            out: list[WhichWaiting] = []
            drop: list[str] = []
            for r in rows:
                other = self._end(entities, r["other_id"])
                live: dict[str, str] = {}
                for candidate in r["candidates"]:
                    end = self._end(entities, candidate)
                    if end is not None and entities[end]["status"] != "rejected" and end != other:
                        live[end] = str(entities[end]["name"])
                # A pronoun's question is never dropped for want of names: "Someone else" keeps its
                # connection (#2790), so it waits even when every name it offered is gone.
                needs = 0 if is_pronoun(str(r["word"])) else 2
                if other is None or entities[other]["status"] == "rejected" or len(live) < needs:
                    drop.append(str(r["id"]))
                    continue
                out.append(
                    WhichWaiting(
                        str(r["id"]), other, str(entities[other]["name"]),
                        bool(r["other_is_subject"]), str(r["predicate"]), str(r["word"]),
                        tuple(live.items()), str(r["quote"]), str(r["detail"]), str(r["upload"]),
                    )
                )  # fmt: skip
            if drop:
                await conn.execute(
                    "DELETE FROM backstory_which WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = ANY(%s)",
                    (guild_id, campaign_id, drop),
                )
        return out

    async def pick_which(
        self,
        guild_id: int,
        campaign_id: str,
        which_id: str,
        entity_id: str | None,
        *,
        outside: bool = False,
    ) -> str:
        """The DM's answer: one of the candidates (the connection joins the waiting ones, ready
        as soon as both its names are answered) or None for "Neither" (it is dropped). `GONE` when
        the question is gone or the pick is not one of its candidates, `FULL` when the queue is.
        `outside`: the entry is not one of the candidates (an unnamed person made for "none of
        these", #2790): the connection still joins the waiting ones. Under the campaign lock, so
        two presses answer once."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "SELECT other_id, other_is_subject, predicate, candidates, quote, secret, detail,"
                " truth, upload, created_at FROM backstory_which WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, which_id),
            )
            row = await cur.fetchone()
            if row is None:
                return GONE
            if entity_id is not None:
                entities = await self._entities(
                    conn, guild_id, campaign_id, {entity_id, *row["candidates"]}
                )
                ends = {self._end(entities, c) for c in row["candidates"]}
                end = self._end(entities, entity_id)
                if (
                    end is None
                    or (end not in ends and not outside)
                    or entities[end]["status"] == "rejected"
                ):
                    return GONE
                if end == row["other_id"] and not outside:
                    return GONE  # (the question's own end is no answer: the question stays)
                cur = await conn.execute(
                    "SELECT count(*) AS n FROM backstory_links WHERE guild_id = %s"
                    " AND campaign_id = %s",
                    (guild_id, campaign_id),
                )
                counted = await cur.fetchone()
                full = counted is not None and int(counted["n"]) >= MAX_WAITING
                if full and end != row["other_id"]:
                    return (
                        FULL  # the cap holds for picks too: nothing was inserted, nothing deleted
                    )
                if end != row["other_id"]:
                    a, b = (
                        (row["other_id"], end)
                        if row["other_is_subject"]
                        else (end, row["other_id"])
                    )
                    await conn.execute(
                        "INSERT INTO backstory_links (guild_id, campaign_id, subject_id, predicate,"
                        " object_id, quote, secret, detail, truth, created_at, upload)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT DO NOTHING",
                        (guild_id, campaign_id, a, row["predicate"], b, row["quote"],
                         row["secret"], row["detail"], row["truth"], row["created_at"],
                         row["upload"]),
                    )  # fmt: skip
            await conn.execute(
                "DELETE FROM backstory_which WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, which_id),
            )
        return DONE

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
                " created_at, upload, told"
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
                        str(r["upload"]), bool(r["told"]),
                    )
                )  # fmt: skip
            # Deletes first, then inserts: a moved row may land on a key a dropped row had.
            for r in drop:
                await self._drop(conn, guild_id, campaign_id, r)
            for a, predicate, b, r in move:
                await conn.execute(
                    "INSERT INTO backstory_links (guild_id, campaign_id, subject_id,"
                    " predicate, object_id, quote, secret, detail, truth, created_at, id, upload,"
                    " told)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, a, predicate, b, r["quote"], r["secret"],
                     r["detail"], r["truth"], r["created_at"], r["id"], r["upload"], r["told"]),
                )  # fmt: skip
        return out

    async def counts(self, guild_id: int, campaign_id: str) -> list[tuple[str, str, bool, int]]:
        """(relationship, upload, ready, how many) for what waits, read-only (nothing is cleaned
        or moved), for the no-AI check `devtools.backstory_links` (#2650). A connection to a
        name that was merged away counts as not ready here: the real queue follows the merge when
        it is next read."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT l.predicate, l.upload,"
                " COALESCE(s.status = 'confirmed' AND o.status = 'confirmed', false) AS ready,"
                " count(*) AS n FROM backstory_links l"
                " LEFT JOIN memory_entities s ON s.guild_id = l.guild_id AND s.campaign_id ="
                " l.campaign_id AND s.id = l.subject_id"
                " LEFT JOIN memory_entities o ON o.guild_id = l.guild_id AND o.campaign_id ="
                " l.campaign_id AND o.id = l.object_id"
                " WHERE l.guild_id = %s AND l.campaign_id = %s"
                " GROUP BY l.predicate, l.upload, ready ORDER BY l.upload, l.predicate, ready",
                (guild_id, campaign_id),
            )
            return [
                (str(r["predicate"]), str(r["upload"]), bool(r["ready"]), int(r["n"]))
                for r in await cur.fetchall()
            ]

    async def take_ready_notices(
        self, guild_id: int, campaign_id: str, *, symmetric: Collection[str] = ()
    ) -> Taken:
        """The connections that became ready since the DM screen last said so (#2650), as a count
        for each upload ("" for an old one), marked told as they are taken: said once, even if two
        rounds overlap. Counts only: the DM screen never shows the connections themselves."""
        waiting = await self.waiting(guild_id, campaign_id, symmetric=symmetric)
        fresh = [w for w in waiting if w.ready and not w.told and w.id]
        if not fresh:
            return Taken({}, [])
        counts: dict[str, int] = {}
        ids: list[str] = []
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, campaign_id)
            cur = await conn.execute(
                "UPDATE backstory_links SET told = true WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s) AND NOT told RETURNING id, upload",
                (guild_id, campaign_id, [w.id for w in fresh]),
            )
            for row in await cur.fetchall():
                ids.append(str(row["id"]))
                counts[str(row["upload"])] = counts.get(str(row["upload"]), 0) + 1
        return Taken(counts, ids)

    async def mark_told(self, guild_id: int, campaign_id: str, ids: Sequence[str]) -> None:
        """These connections were just announced some other way (an upload's own summary)."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE backstory_links SET told = true WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s)",
                (guild_id, campaign_id, list(ids)),
            )

    async def untell(self, guild_id: int, campaign_id: str, ids: Sequence[str]) -> None:
        """Say again later: the notice for these could not be posted."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE backstory_links SET told = false WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s)",
                (guild_id, campaign_id, list(ids)),
            )

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
