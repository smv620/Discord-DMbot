"""The DM's question when a character joins the party (#4537, party knowledge slice 2b;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, 2026-11-18).

"Mira has joined the party. What does Mira know about what came before?" A character who joins a
party that already has members knows only common knowledge, their own backstory and what is said
at the table from then on, until the DM answers (rule 13.4: a join writes no who-knows rows except
by the DM's answer). Rules kept here:
- **Asked in the joining change** (`ask`, from `knowers.follow_flags` with `DMBOT_PARTY_JOIN` on),
  so an Undo of the join takes a question still waiting (`undo`).
- **Never sent during play:** `claim` takes nothing while one of the campaign's sessions is
  running; the after-session steps send it after, and a DM's own action outside play (the
  since-when prompt, a "plays" yes, opening ❓ Questions for you) sends it at once. A character
  made some other way between sessions is asked after the next session, and counted on ❓
  Questions for you meanwhile. **Sent once:** `claim`
  marks the rows in the same statement that reads them. Characters who joined before it went share
  one message (`group_id`).
- **An unanswered question stays** in ❓ Questions for you (`count_waiting`), never sent again.
- **The answers** are the store's (`MemoryStore.join_answer_*`): `everyone` writes nothing;
  `party` and `first` copy what the party knows in one change, whose Undo this module follows.
Ids and times only, never a word of the story. Not logged: a question is not memory, and the
change it rode on (`batch`) or answered with (`answered_batch`) is what Undo follows.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import Changes
from dmbot.memory.models import MemoryRuleError

WAITING = "waiting"
EVERYONE = "everyone"
PARTY = "party"
FIRST = "first"


class AlreadyAnswered(MemoryRuleError):
    """Another DM answered this question first (or the same answer was pressed twice)."""


@dataclass(frozen=True, slots=True)
class Question:
    """One character's join question."""

    id: str
    entity_id: str
    status: str
    group_id: str | None
    sent_at: int | None
    created_at: int


_COLUMNS = "id, entity_id, status, group_id, sent_at, created_at"
# A question waits only while its character is still a confirmed player character in the party
# (an open stretch): one who left or was made an NPC meanwhile is neither counted nor sent.
_IN_PARTY = (
    " AND EXISTS (SELECT 1 FROM memory_party_spans p WHERE p.guild_id = q.guild_id"
    "  AND p.campaign_id = q.campaign_id AND p.entity_id = q.entity_id AND p.to_at IS NULL)"
    " AND EXISTS (SELECT 1 FROM memory_entities e WHERE e.guild_id = q.guild_id"
    "  AND e.campaign_id = q.campaign_id AND e.id = q.entity_id AND e.type = 'character'"
    "  AND e.role = 'player_character' AND e.status = 'confirmed')"
)


def _question(row: Any) -> Question:
    return Question(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        status=str(row["status"]),
        group_id=None if row["group_id"] is None else str(row["group_id"]),
        sent_at=None if row["sent_at"] is None else int(row["sent_at"]),
        created_at=int(row["created_at"]),
    )


async def ask(w: Changes, entity_ids: Sequence[str]) -> None:
    """Ask about these characters, who joined in this change (one question each, one message for
    all of them when sent). A character with a question already waiting is not asked twice."""
    if not entity_ids:
        return
    rows = [(secrets.token_hex(16), e) for e in dict.fromkeys(entity_ids)]
    cur = await w.conn.execute(
        "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id, created_at)"
        " SELECT %s, %s, q.id, q.entity_id, %s FROM unnest(%s::text[], %s::text[])"
        " AS q(id, entity_id) ON CONFLICT DO NOTHING RETURNING id",
        (*w.ids, w.now, [r[0] for r in rows], [r[1] for r in rows]),
    )
    w.join_asked.extend(str(r["id"]) for r in await cur.fetchall())


async def set_batch(w: Changes, question_ids: Sequence[str], batch: int) -> None:
    """The change that asked these (`MemoryStore._write`, once the change has its number)."""
    await w.conn.execute(
        "UPDATE memory_join_questions SET batch = %s WHERE guild_id = %s AND campaign_id = %s"
        " AND id = ANY(%s)",
        (batch, *w.ids, list(question_ids)),
    )


async def undo(w: Changes, batch: int) -> None:
    """An Undo of `batch` (inside its write): a question it asked that still waits goes with it;
    a "What the party knows" it was reads as "Only what everyone knows" again (the ruling's
    Undo words), and a "From the first session" it was waits again (its stretch moved back)."""
    await w.conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND batch = %s AND status = 'waiting'",
        (*w.ids, batch),
    )
    await w.conn.execute(
        "UPDATE memory_join_questions SET status = CASE status WHEN 'party' THEN 'everyone'"
        " ELSE 'waiting' END, answered_batch = NULL"
        " WHERE guild_id = %s AND campaign_id = %s AND answered_batch = %s"
        " AND status IN ('party', 'first')",
        (*w.ids, batch),
    )


async def claim(conn: Any, guild_id: int, campaign_id: str, now: int) -> list[Question]:
    """Take the questions not sent yet, to send them as one message: none while a session of the
    campaign is running (never during play). The rows are marked in the statement that reads
    them, so two callers never send the same one."""
    cur = await conn.execute(
        "UPDATE memory_join_questions q SET group_id = %s, sent_at = %s"
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'"
        " AND q.sent_at IS NULL"
        + _IN_PARTY
        + " AND NOT EXISTS (SELECT 1 FROM transcript_sessions s WHERE s.guild_id = %s"
        "  AND s.campaign_id = %s AND s.ended_at IS NULL)"
        f" RETURNING {_COLUMNS}",
        (secrets.token_hex(16), now, guild_id, campaign_id, guild_id, campaign_id),
    )
    return sorted((_question(r) for r in await cur.fetchall()), key=lambda q: (q.created_at, q.id))


async def unclaim(conn: Any, guild_id: int, campaign_id: str, group_id: str) -> None:
    """Give back a claimed message that reached no DM: it is sent again next time."""
    await conn.execute(
        "UPDATE memory_join_questions SET group_id = NULL, sent_at = NULL WHERE guild_id = %s"
        " AND campaign_id = %s AND group_id = %s AND status = 'waiting'",
        (guild_id, campaign_id, group_id),
    )


async def group(conn: Any, guild_id: int, campaign_id: str, group_id: str) -> list[Question]:
    """The questions sent together as one message, oldest first."""
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND group_id = %s ORDER BY created_at, id",
        (guild_id, campaign_id, group_id),
    )
    return [_question(r) for r in await cur.fetchall()]


async def waiting_groups(conn: Any, guild_id: int, campaign_id: str) -> list[str]:
    """The sent messages still waiting for an answer, oldest first (❓ Questions for you)."""
    cur = await conn.execute(
        "SELECT q.group_id, min(q.created_at) AS at FROM memory_join_questions q"
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'"
        " AND q.group_id IS NOT NULL" + _IN_PARTY + " GROUP BY q.group_id ORDER BY at, q.group_id",
        (guild_id, campaign_id),
    )
    return [str(r["group_id"]) for r in await cur.fetchall()]


async def waiting_for(conn: Any, guild_id: int, campaign_id: str, entity_id: str) -> bool:
    """Whether this character has a join question waiting (the since-when prompt asks only then)."""
    cur = await conn.execute(
        "SELECT 1 FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND entity_id = %s AND status = 'waiting' LIMIT 1",
        (guild_id, campaign_id, entity_id),
    )
    return await cur.fetchone() is not None


async def waiting_entities(conn: Any, guild_id: int, campaign_id: str) -> set[str]:
    """The characters whose own join question is still unanswered: not yet caught up, so left
    out of "what the party knows" (Supervisor's ruling on #4780)."""
    cur = await conn.execute(
        "SELECT DISTINCT entity_id FROM memory_join_questions WHERE guild_id = %s"
        " AND campaign_id = %s AND status = 'waiting'",
        (guild_id, campaign_id),
    )
    return {str(r["entity_id"]) for r in await cur.fetchall()}


async def count_waiting(conn: Any, guild_id: int, campaign_id: str) -> tuple[int, int | None]:
    """How many characters wait for the DM's answer, and since when (the board's count)."""
    cur = await conn.execute(
        "SELECT count(*) AS n, min(q.created_at) AS oldest FROM memory_join_questions q"
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'" + _IN_PARTY,
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    if row is None:
        return 0, None
    return int(row["n"]), None if row["oldest"] is None else int(row["oldest"])


__all__ = [
    "EVERYONE",
    "FIRST",
    "PARTY",
    "WAITING",
    "AlreadyAnswered",
    "Question",
    "ask",
    "claim",
    "count_waiting",
    "group",
    "set_batch",
    "unclaim",
    "undo",
    "waiting_entities",
    "waiting_for",
    "waiting_groups",
]
