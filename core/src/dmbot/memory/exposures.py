"""Secrets said or typed at the table, recorded for the DM's summary (#2410, docs/PLAN.md,
"Secrets said out loud are revealed too, and the DM can recant"). The rule: **nothing becomes
known to the players before the DM is told.** This module only finds and remembers; it applies
nothing, so a player's reader sees no change until the after-session summary goes out.

Finding is pure code with no AI. A secret name is found by `secret_aloud.spoken` (whole names, the
way names are compared everywhere). A secret connection is found when one line names both of its
ends, by any of their names. Every table word of a person recorded now counts, the DM's own too
(the DM saying a secret by mistake is the first case); never the DM sidebar, the DM screen or
anyone who stopped (`secret_aloud.said_lines`).

What is kept is which secret and how often, never a line and never who said it: a name as its
spelling key, a connection as its id. An exposure the DM already answered another way (the live
"Is it revealed now?", or a question about the name) is settled, and a settled one is not
recorded again.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from psycopg import errors

from dmbot.db import Conn, Database
from dmbot.memory.models import spelling_key
from dmbot.memory.secret_aloud import MIN_KEY, SecretName, spoken
from dmbot.memory.store import MemoryStore


@dataclass(frozen=True, slots=True)
class SecretConnection:
    relation_id: str
    subject: tuple[str, ...]  # every name of the subject (the main name and the other names)
    object: tuple[str, ...]  # and of the object


@dataclass(frozen=True, slots=True)
class Exposure:
    kind: str  # "name" or "connection"
    spelling: str | None  # a name's spelling key
    relation_id: str | None
    times: int


def _pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"(?<=\s){re.escape(key)}(?=\s)")


def _inside(small: str, big: str) -> bool:
    """`small` is `big` or some whole words of it ("rook" in "captain rook")."""
    return f" {small} " in f" {big} "


def _apart(a: set[str], b: set[str]) -> tuple[set[str], set[str]]:
    """The two ends' names with the ones that could be told apart by the words alone: a name both
    ends share ("the stranger") or one that sits inside a name of the other end ("Rook" in
    "Captain Rook") is left out of both, or one phrase would seem to name both ends."""
    shared = {x for x in a for y in b if _inside(x, y) or _inside(y, x)}
    shared |= {y for x in a for y in b if _inside(x, y) or _inside(y, x)}
    return a - shared, b - shared


def named(connections: Sequence[SecretConnection], texts: Iterable[str]) -> dict[str, int]:
    """For each secret connection, how many lines name both its ends (any name of each). A name
    shorter than `MIN_KEY` is too common a word to look for."""
    wanted: dict[str, re.Pattern[str]] = {}
    ends: list[tuple[str, set[str], set[str]]] = []
    for c in connections:
        a = {spelling_key(n) for n in c.subject}
        b = {spelling_key(n) for n in c.object}
        a = {k for k in a if len(k) >= MIN_KEY}
        b = {k for k in b if len(k) >= MIN_KEY}
        a, b = _apart(a, b)
        if a and b:
            ends.append((c.relation_id, a, b))
            for k in a | b:
                wanted.setdefault(k, _pattern(k))
    counts: dict[str, int] = {}
    for text in texts:
        padded = f" {spelling_key(text)} "
        here = {k for k, p in wanted.items() if k in padded and p.search(padded)}
        if len(here) < 2:
            continue
        for relation_id, a, b in ends:
            if here & a and here & b:
                counts[relation_id] = counts.get(relation_id, 0) + 1
    return counts


async def secret_connections(
    memory: MemoryStore, guild_id: int, campaign_id: str
) -> list[SecretConnection]:
    """This campaign's confirmed secret connections between confirmed, live entries, with every
    name of each end, read for this server and campaign only. A connection that has a player or
    DM entry (`type = 'person'`) at an end is not looked for: those entries are the table's own
    people, whose connections are not story secrets."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT r.id AS rid, r.subject_id AS s, r.object_id AS o FROM memory_relations r"
            " JOIN memory_entities a ON a.guild_id = r.guild_id AND a.campaign_id = r.campaign_id"
            "  AND a.id = r.subject_id"
            " JOIN memory_entities b ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id"
            "  AND b.id = r.object_id"
            " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.secret AND r.status = 'confirmed'"
            " AND a.status = 'confirmed' AND b.status = 'confirmed'"
            " AND a.type <> 'person' AND b.type <> 'person' ORDER BY r.id",
            scope.ids,
        )
        found = await cur.fetchall()
        if not found:
            return []
        wanted = {str(r[end]) for r in found for end in ("s", "o")}
        cur = await scope.conn.execute(
            "SELECT entity_id, text FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
            " AND entity_id = ANY(%s) AND status = 'confirmed' ORDER BY id",
            (*scope.ids, list(wanted)),
        )
        names: dict[str, list[str]] = {}
        for r in await cur.fetchall():
            names.setdefault(str(r["entity_id"]), []).append(str(r["text"]))
        cur = await scope.conn.execute(
            "SELECT id, name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND id = ANY(%s)",
            (*scope.ids, list(wanted)),
        )
        for r in await cur.fetchall():
            names.setdefault(str(r["id"]), []).append(str(r["name"]))
        return [
            SecretConnection(
                str(r["rid"]),
                tuple(dict.fromkeys(names.get(str(r["s"]), ()))),
                tuple(dict.fromkeys(names.get(str(r["o"]), ()))),
            )
            for r in found
        ]


def find_exposures(
    secret_names: Sequence[SecretName],
    connections: Sequence[SecretConnection],
    texts: Sequence[str],
) -> list[Exposure]:
    """Every secret name and secret connection the players' words give away, names first (most
    said first), then connections. All of them: the summary caps what it shows."""
    out = [
        Exposure("name", spelling_key(s.text), None, s.times) for s in spoken(secret_names, texts)
    ]
    counts = named(connections, texts)
    out += [Exposure("connection", None, rid, n) for rid, n in sorted(counts.items())]
    return out


@dataclass(frozen=True, slots=True)
class Waiting:
    """One row of the table: what the summary lists and the Recant button points at."""

    id: int
    kind: str
    spelling: str | None
    relation_id: str | None
    times: int
    status: str
    batch: int | None


async def describe(
    memory: MemoryStore, guild_id: int, campaign_id: str, rows: Sequence[Waiting]
) -> list[tuple[Waiting, str]]:
    """Each waiting exposure with the plain words the summary shows for it, in order. One that is
    no longer a secret (the DM changed it meanwhile), or whose connection is gone, is left out:
    there is nothing to tell. A name: the name as the DM wrote it. A connection: both ends and
    how they are tied."""
    from dmbot.memory.secret_aloud import secret_names

    text_of: dict[str, str] = {}
    for secret in await secret_names(memory, guild_id, campaign_id):
        text_of.setdefault(spelling_key(secret.text), secret.text)
    ties: dict[str, str] = {}
    wanted = [r.relation_id for r in rows if r.relation_id]
    if wanted:
        async with memory._read(guild_id, campaign_id) as scope:
            join = (
                " ON {x}.guild_id = r.guild_id AND {x}.campaign_id = r.campaign_id"
                " AND {x}.id = r.{e}"
            )
            cur = await scope.conn.execute(
                "SELECT r.id, a.name AS a, b.name AS b, r.predicate, p.label"
                " FROM memory_relations r"
                " JOIN memory_entities a" + join.format(x="a", e="subject_id") +
                " JOIN memory_entities b" + join.format(x="b", e="object_id") +
                " LEFT JOIN memory_predicates p ON p.guild_id = r.guild_id"
                " AND p.campaign_id = r.campaign_id AND p.key = r.predicate"
                " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.id = ANY(%s) AND r.secret"
                " AND r.predicate <> 'led_to'"
                " AND r.status = 'confirmed'",
                (*scope.ids, wanted),
            )  # fmt: skip
            for r in await cur.fetchall():
                how = str(r["label"] or r["predicate"]).replace("_", " ")
                ties[str(r["id"])] = f"{r['a']} and {r['b']} ({how})"
    out: list[tuple[Waiting, str]] = []
    for row in rows:
        if row.kind == "name" and row.spelling in text_of:
            out.append((row, f"{text_of[row.spelling]} (a name)"))
        elif row.kind == "connection" and row.relation_id in ties:
            out.append((row, ties[row.relation_id]))
    return out


class ExposureStore:
    """The pending exposures, one campaign at a time (every statement names the server and the
    campaign, and the table has row-level security on top)."""

    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def record(
        self, guild_id: int, campaign_id: str, session_at: int, found: Sequence[Exposure]
    ) -> int:
        """Remember what a session gave away. Run again for the same session it updates the
        counts of what is still pending and leaves what was settled alone. Returns how many
        are pending for the session."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            for e in found:
                # a savepoint each: a connection deleted meanwhile must not lose the other rows
                try:
                    async with conn.transaction():
                        await self._put(conn, guild_id, campaign_id, session_at, e, now)
                except errors.ForeignKeyViolation:
                    continue
            cur = await conn.execute(
                "SELECT count(*) AS n FROM secret_exposures"
                " WHERE guild_id = %s AND campaign_id = %s AND session_started_at = %s"
                " AND status = 'pending'",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    @staticmethod
    async def _put(
        conn: Conn, guild_id: int, campaign_id: str, session_at: int, e: Exposure, now: int
    ) -> None:
        if e.kind == "name":
            await conn.execute(
                "INSERT INTO secret_exposures (guild_id, campaign_id, session_started_at,"
                " kind, spelling, times, created_at) VALUES (%s, %s, %s, 'name', %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_started_at, spelling)"
                " WHERE kind = 'name' DO UPDATE SET times = excluded.times"
                " WHERE secret_exposures.status = 'pending'",
                (guild_id, campaign_id, session_at, e.spelling, e.times, now),
            )
        else:
            await conn.execute(
                "INSERT INTO secret_exposures (guild_id, campaign_id, session_started_at,"
                " kind, relation_id, times, created_at)"
                " VALUES (%s, %s, %s, 'connection', %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_started_at, relation_id)"
                " WHERE kind = 'connection' DO UPDATE SET times = excluded.times"
                " WHERE secret_exposures.status = 'pending'",
                (guild_id, campaign_id, session_at, e.relation_id, e.times, now),
            )

    async def pending(
        self, guild_id: int, campaign_id: str, session_at: int | None = None
    ) -> list[Exposure]:
        """What is still waiting (for one session, or all), most said first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT kind, spelling, relation_id, times FROM secret_exposures"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'pending'"
                " AND (%s::bigint IS NULL OR session_started_at = %s)"
                " ORDER BY kind DESC, times DESC, id",
                (guild_id, campaign_id, session_at, session_at),
            )
            return [
                Exposure(r["kind"], r["spelling"], r["relation_id"], int(r["times"]))
                for r in await cur.fetchall()
            ]

    async def settle_name(
        self, guild_id: int, campaign_id: str, text: str, session_at: int | None = None
    ) -> int:
        """The DM answered about this name another way: it is no longer waiting, in any session.
        `session_at`: the session that is running now, when the answer comes during it (the live
        "Is it revealed now?"): the exposure is only recorded when the session ends, so a settled
        mark is put down for it now, and the recording leaves a settled row alone."""
        key = spelling_key(text)
        settled = await self._settle(guild_id, campaign_id, "spelling", key, "name")
        if session_at is not None and len(key) >= MIN_KEY:
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "INSERT INTO secret_exposures (guild_id, campaign_id, session_started_at,"
                    " kind, spelling, times, status, created_at)"
                    " VALUES (%s, %s, %s, 'name', %s, 1, 'settled', %s)"
                    " ON CONFLICT (guild_id, campaign_id, session_started_at, spelling)"
                    " WHERE kind = 'name' DO UPDATE SET status = 'settled'",
                    (guild_id, campaign_id, session_at, key, int(self._clock())),
                )
        return settled

    async def settle_alias(
        self, guild_id: int, campaign_id: str, alias_id: str, session_at: int | None = None
    ) -> int:
        """The same, for the name a question's button carries (an alias id)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT text FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (guild_id, campaign_id, alias_id),
            )
            row = await cur.fetchone()
        return (
            0
            if row is None
            else await self.settle_name(guild_id, campaign_id, row["text"], session_at)
        )

    async def settle_connection(self, guild_id: int, campaign_id: str, relation_id: str) -> int:
        return await self._settle(guild_id, campaign_id, "relation_id", relation_id, "connection")

    async def _settle(
        self, guild_id: int, campaign_id: str, column: str, key: str, kind: str
    ) -> int:
        assert column in (
            "spelling",
            "relation_id",
        )  # (only this module's two columns are formatted in)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"UPDATE secret_exposures SET status = 'settled' WHERE guild_id = %s"
                f" AND campaign_id = %s AND kind = %s AND {column} = %s AND status = 'pending'",
                (guild_id, campaign_id, kind, key),
            )
            return cur.rowcount

    async def waiting(self, guild_id: int, campaign_id: str) -> list[Waiting]:
        """The exposures still waiting to be applied, from this and earlier sessions, names then
        connections, most said first (what a summary left out last time comes first again)."""
        return await self._rows(guild_id, campaign_id, "status = 'pending'", [], "")

    async def one(self, guild_id: int, campaign_id: str, exposure_id: int) -> Waiting | None:
        """A row by its id, in this campaign only (what a Recant button carries)."""
        rows = await self._rows(guild_id, campaign_id, "id = %s", [exposure_id], "")
        return rows[0] if rows else None

    async def _rows(
        self, guild_id: int, campaign_id: str, where: str, params: list[int], extra: str
    ) -> list[Waiting]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, kind, spelling, relation_id, times, status, batch FROM secret_exposures"
                f" WHERE guild_id = %s AND campaign_id = %s AND {where} {extra}"
                " ORDER BY kind DESC, times DESC, id",
                (guild_id, campaign_id, *params),
            )
            return [
                Waiting(
                    int(r["id"]), r["kind"], r["spelling"], r["relation_id"], int(r["times"]),
                    r["status"], r["batch"],
                )
                for r in await cur.fetchall()
            ]  # fmt: skip

    # Where a row may come from for each state it moves to: a second press or a second run
    # matches nothing, and a row is claimed (`applying`) before its secret is shown.
    _FROM: ClassVar[dict[str, tuple[str, ...]]] = {
        "applying": ("pending",),
        "pending": ("applying",),
        "applied": ("applying",),
        "settled": ("pending", "applying"),
        "recanted": ("applied", "pending"),
    }

    async def mark(
        self,
        guild_id: int,
        campaign_id: str,
        ids: Sequence[int],
        status: str,
        batch: int | None = None,
    ) -> int:
        """Move rows to `status` (`applied` with the change-log batch that showed them). Only a
        row in a state it may come from moves, and the count says how many did."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE secret_exposures SET status = %s, batch = COALESCE(%s, batch)"
                " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)"
                " AND status = ANY(%s)",
                (status, batch, guild_id, campaign_id, list(ids), list(self._FROM[status])),
            )
            return cur.rowcount
