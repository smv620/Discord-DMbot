"""Typed evidence links (#2662, docs/STORY_MEMORY.md 9a, "Evidence links"): what a connection or a
belief stands on, as rows of `memory_evidence`, each a claim, a transcript line, a clue or an
event of the one campaign.

Three rules from the contract live here:
- **A link stays in its campaign.** The database keeps its owner (a connection or a belief) in the
  campaign; `add_in` refuses a target that is not this campaign's, so another campaign's ids can
  never be linked.
- **A person's words leave no trace in a link.** When a line or claim is removed, its link becomes
  a placeholder: the target is cleared, the kind and the session stay. A line's target is its
  reference ("session:started_ms:user"), which names the speaker, so clearing it is what keeps the
  speaker out. Nothing here ever holds the words themselves.
- **A link never widens access.** It is read only with the connection or belief that owns it,
  through the door that already shows that.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg import sql

from dmbot.db import Conn
from dmbot.memory._changes import Changes, Scope
from dmbot.memory.models import MemoryRuleError

CLAIM, LINE, CLUE, EVENT = "claim", "line", "clue", "event"
KINDS = (CLAIM, LINE, CLUE, EVENT)
TARGET_MAX = 100  # the column's limit; a line reference is well under it
PER_OWNER_MAX = 20  # as many as a belief's old list held

NOT_THIS_CAMPAIGN = "That doesn't belong to this campaign."
BAD_LINK = "That evidence is not in the right shape."

# How a target is found in ONE campaign, by kind, for the target in `%(t)s`. The same text checks a
# new link and counts the ones that no longer resolve.
_RESOLVES = {
    CLAIM: "EXISTS (SELECT 1 FROM memory_claims x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id = %(c)s AND x.id = %(t)s)",
    CLUE: "EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id = %(c)s AND x.id = %(t)s AND x.type = 'clue')",
    EVENT: "EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id = %(c)s AND x.id = %(t)s AND x.type = 'event')",
    # A line is "session:started_ms:user" (`transcript.models.line_ref`); the session must be this
    # campaign's. (The line itself may be gone already: that is a placeholder's business.)
    LINE: "EXISTS (SELECT 1 FROM transcript_sessions x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id = %(c)s"
    " AND x.id = substring(%(t)s from '^(.*):[0-9]+:[0-9]+$'))",
}


@dataclass(frozen=True, slots=True)
class Link:
    """One piece of evidence. `target_id` is None for a placeholder."""

    id: str
    kind: str
    target_id: str | None
    session_started_at: int | None
    added_at: int

    @property
    def placeholder(self) -> bool:
        return self.target_id is None


def _link(r: dict[str, Any]) -> Link:
    return Link(
        str(r["id"]), str(r["kind"]), r["target_id"], r["session_started_at"], int(r["added_at"])
    )


def words_for(link: Link, session_number: int | None = None) -> str:
    """What to say of a placeholder: the kind and the session, never a word or a speaker."""
    noun = {CLAIM: "a claim", LINE: "a line", CLUE: "a clue", EVENT: "an event"}[link.kind]
    where = f" from session {session_number}" if session_number is not None else ""
    return f"{noun}{where}, no longer available"


async def resolves(conn: Conn, guild_id: int, campaign_id: str, kind: str, target: str) -> bool:
    cur = await conn.execute(
        f"SELECT {_RESOLVES[kind]} AS ok", {"g": guild_id, "c": campaign_id, "t": target}
    )
    row = await cur.fetchone()
    return bool(row and row["ok"])


async def add_in(
    w: Changes,
    *,
    kind: str,
    target_id: str,
    relation_id: str | None = None,
    belief_id: str | None = None,
    session_started_at: int | None = None,
    trusted: bool = False,
) -> Link:
    """Link a connection or a belief of this campaign to one thing it stands on, inside a write
    that is already open. The same link twice is kept once. A target from another campaign (or
    one that does not exist) is refused. `trusted`: the target came from a row of this campaign
    that the caller just read (a mention's line, a belief's earlier evidence), so it is not looked
    up again."""
    if kind not in KINDS or (relation_id is None) == (belief_id is None):
        raise MemoryRuleError(BAD_LINK)
    if not target_id or len(target_id) > TARGET_MAX:
        raise MemoryRuleError(BAD_LINK)
    if not trusted and not await resolves(w.conn, w.guild_id, w.campaign_id, kind, target_id):
        raise MemoryRuleError(NOT_THIS_CAMPAIGN)
    owner, owner_id = ("relation_id", relation_id) if relation_id else ("belief_id", belief_id)
    cur = await w.conn.execute(
        sql.SQL(
            "SELECT count(*) AS n FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s"
            " AND {} = %s"
        ).format(sql.Identifier(owner)),
        (*w.ids, owner_id),
    )
    row = await cur.fetchone()
    assert row is not None
    if int(row["n"]) >= PER_OWNER_MAX and not trusted:  # (a copy of what was already there)
        raise MemoryRuleError(BAD_LINK)
    cur = await w.conn.execute(
        sql.SQL(
            "INSERT INTO memory_evidence (guild_id, campaign_id, {}, kind, target_id,"
            " session_started_at, added_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING"
        ).format(sql.Identifier(owner)),
        (*w.ids, owner_id, kind, target_id, session_started_at, w.now),
    )
    cur = await w.conn.execute(
        sql.SQL(
            "SELECT id, kind, target_id, session_started_at, added_at FROM memory_evidence"
            " WHERE guild_id = %s AND campaign_id = %s AND {} = %s AND kind = %s"
            " AND target_id = %s"
        ).format(sql.Identifier(owner)),
        (*w.ids, owner_id, kind, target_id),
    )
    made = await cur.fetchone()
    assert made is not None
    return _link(made)


async def for_owners(
    scope: Scope, *, relation_ids: Sequence[str] = (), belief_ids: Sequence[str] = ()
) -> dict[str, list[Link]]:
    """The links of these connections and beliefs, by owner id, oldest first. Callers only ask
    for owners they are already allowed to show."""
    out: dict[str, list[Link]] = {}
    for column, ids in (("relation_id", relation_ids), ("belief_id", belief_ids)):
        if not ids:
            continue
        cur = await scope.conn.execute(
            sql.SQL(
                "SELECT {col} AS owner, id, kind, target_id, session_started_at, added_at"
                " FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s"
                " AND {col} = ANY(%s) ORDER BY added_at, id"
            ).format(col=sql.Identifier(column)),
            (*scope.ids, list(ids)),
        )
        for r in await cur.fetchall():
            out.setdefault(str(r["owner"]), []).append(_link(r))
    return out


async def placeholder_lines(conn: Conn, guild_id: int, refs: Collection[str]) -> int:
    """These transcript lines are gone: every link to one keeps its kind and session and loses its
    target, in every campaign of the server. Returns how many links changed."""
    return await _placeholder(conn, guild_id, LINE, refs)


async def placeholder_claims(conn: Conn, guild_id: int, claim_ids: Collection[str]) -> int:
    """These claims are gone (they cited a person who stopped being recorded): same."""
    return await _placeholder(conn, guild_id, CLAIM, claim_ids)


async def _placeholder(conn: Conn, guild_id: int, kind: str, targets: Collection[str]) -> int:
    changed = 0
    ordered = sorted(set(targets))
    for i in range(0, len(ordered), 1000):  # (a long deletion is many lines: small statements)
        batch = ordered[i : i + 1000]
        cur = await conn.execute(
            "UPDATE memory_evidence SET target_id = NULL WHERE guild_id = %s AND kind = %s"
            " AND target_id = ANY(%s)",
            (guild_id, kind, batch),
        )
        changed += cur.rowcount
    if ordered:  # (once for all of them: the lists and the log have no index on a reference)
        await _scrub_old_lists(conn, guild_id, ordered)
    return changed


async def _scrub_old_lists(conn: Conn, guild_id: int, targets: list[str]) -> None:
    """Until the old lists are dropped, a belief's `evidence_ids` (and the undo log's copy of the
    row) can still hold a removed line's reference: take it out there too. A cheap look first,
    because on a live table those lists are empty."""
    cur = await conn.execute(
        "SELECT 1 FROM memory_beliefs WHERE guild_id = %s AND evidence_ids && %s LIMIT 1",
        (guild_id, targets),
    )
    if await cur.fetchone() is not None:
        await conn.execute(
            "UPDATE memory_beliefs SET evidence_ids = ARRAY("
            "  SELECT x FROM unnest(evidence_ids) AS x WHERE NOT (x = ANY(%s)))"
            " WHERE guild_id = %s AND evidence_ids && %s",
            (targets, guild_id, targets),
        )
    for column in ("before", "after"):
        await conn.execute(
            sql.SQL(
                "UPDATE memory_changes SET {c} = jsonb_set({c}, '{{evidence_ids}}',"
                " COALESCE((SELECT jsonb_agg(x) FROM jsonb_array_elements_text({c}->'evidence_ids')"
                "   AS x WHERE NOT (x = ANY(%s))), '[]'::jsonb))"
                " WHERE guild_id = %s AND table_name = 'memory_beliefs'"
                " AND {c}->'evidence_ids' ?| %s"
            ).format(c=sql.Identifier(column)),
            (targets, guild_id, targets),
        )


@dataclass(frozen=True, slots=True)
class Audit:
    """Counts only (the health report's way): never a target, a name or a word."""

    links: int
    placeholders: int
    dangling: int  # a target that resolves nowhere
    foreign: int  # a target that is another campaign's

    def line(self) -> str:
        return (
            f"evidence: links={self.links} placeholders={self.placeholders}"
            f" dangling={self.dangling} foreign={self.foreign}"
        )


# For each kind, whether the target resolves in this campaign, and whether it exists in ANOTHER
# campaign of the same server (so it is a foreign link, not just a missing one).
_ELSEWHERE = {
    CLAIM: "EXISTS (SELECT 1 FROM memory_claims x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id <> %(c)s AND x.id = e.target_id)",
    CLUE: "EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id <> %(c)s AND x.id = e.target_id)",
    EVENT: "EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id <> %(c)s AND x.id = e.target_id)",
    LINE: "EXISTS (SELECT 1 FROM transcript_sessions x WHERE x.guild_id = %(g)s"
    " AND x.campaign_id <> %(c)s"
    " AND x.id = substring(e.target_id from '^(.*):[0-9]+:[0-9]+$'))",
}


async def audit(conn: Conn, guild_id: int, campaign_id: str) -> Audit:
    """Count a campaign's links: how many, how many are placeholders, how many point at nothing and
    how many at another campaign's thing. Read only."""
    params = {"g": guild_id, "c": campaign_id}
    cur = await conn.execute(
        "SELECT count(*) AS links, count(*) FILTER (WHERE target_id IS NULL) AS placeholders"
        " FROM memory_evidence WHERE guild_id = %(g)s AND campaign_id = %(c)s",
        params,
    )
    totals = await cur.fetchone()
    assert totals is not None
    dangling = foreign = 0
    for kind in KINDS:
        here = _RESOLVES[kind].replace("%(t)s", "e.target_id")
        cur = await conn.execute(
            f"SELECT count(*) FILTER (WHERE {_ELSEWHERE[kind]}) AS foreign,"
            f" count(*) FILTER (WHERE NOT {_ELSEWHERE[kind]}) AS dangling"
            f" FROM memory_evidence e WHERE e.guild_id = %(g)s AND e.campaign_id = %(c)s"
            f" AND e.kind = %(k)s AND e.target_id IS NOT NULL AND NOT {here}",
            {**params, "k": kind},
        )
        row = await cur.fetchone()
        assert row is not None
        foreign += int(row["foreign"])
        dangling += int(row["dangling"])
    return Audit(int(totals["links"]), int(totals["placeholders"]), dangling, foreign)


async def scrub_claim_lines(
    conn: Conn, guild_id: int, *, refs: Collection[str] = (), user_id: int | None = None
) -> int:
    """Take a removed person out of every claim, in every campaign of the server (#2676, #2679): a
    claim the DM confirmed stays (it is the DM's fact) but no longer names who said what. Their
    line references leave `lines` and their id leaves `speaker_ids`.
    `refs`: these exact references (a line reference is "session:started_ms:user"). The speaker
    they name leaves `speaker_ids` too, unless the claim still cites another line of theirs.
    `user_id`: every reference that ends in this speaker, and the id itself. Returns how many
    claims changed."""
    changed = 0
    ordered = sorted(set(refs))
    affected: set[str] = set()
    for i in range(0, len(ordered), 1000):
        batch = ordered[i : i + 1000]
        cur = await conn.execute(
            "UPDATE memory_claims SET lines = ARRAY("
            "  SELECT x FROM unnest(lines) AS x WHERE NOT (x = ANY(%s)))"
            " WHERE guild_id = %s AND lines && %s RETURNING id",
            (batch, guild_id, batch),
        )
        hit = await cur.fetchall()
        changed += len(hit)
        affected.update(str(r["id"]) for r in hit)
    speakers = {int(r.rsplit(":", 1)[1]) for r in ordered if r.rsplit(":", 1)[-1].isdigit()}
    if affected:
        for speaker in sorted(speakers):
            await conn.execute(
                "UPDATE memory_claims SET speaker_ids = array_remove(speaker_ids, %s)"
                " WHERE guild_id = %s AND id = ANY(%s) AND %s = ANY(speaker_ids)"
                " AND NOT EXISTS (SELECT 1 FROM unnest(lines) AS y WHERE y ~ %s)",
                (speaker, guild_id, sorted(affected), speaker, f":{speaker}$"),
            )
    if user_id is not None:
        cur = await conn.execute(
            "UPDATE memory_claims SET lines = ARRAY("
            "  SELECT x FROM unnest(lines) AS x WHERE x !~ %s),"
            " speaker_ids = array_remove(speaker_ids, %s)"
            " WHERE guild_id = %s AND (%s = ANY(speaker_ids) OR EXISTS ("
            "  SELECT 1 FROM unnest(lines) AS y WHERE y ~ %s))",
            (f":{int(user_id)}$", int(user_id), guild_id, int(user_id), f":{int(user_id)}$"),
        )
        changed += cur.rowcount
    return changed
