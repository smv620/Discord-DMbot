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
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import Changes, Scope
from dmbot.memory.models import MemoryRuleError, TooLateToUndo

WAITING = "waiting"
EVERYONE = "everyone"
PARTY = "party"
FIRST = "first"
CHOSEN = "chosen"  # ("Let me choose": the first pick answered it, #4537 slice 4c, 0558)
JOIN = "join"  # (the question's kind: new to the party)
BACK = "back"  # (or back in it after leaving, #4537 slice 4a)
LEAVE = "leave"  # (a goodbye heard at the table: still in the party? #4537 slice 6, 0570)
DIED = "died"  # (a player character marked dead: still in the party? slice 6a, 0570)
LEFT = "left"  # ("Left the party": the stretch closed, with the card's own call)
STAYS = "stays"  # ("Still in the party": nothing written, never asked again for it)
# The two families: a join or coming back asks what the character knows, a goodbye or a death
# whether they are still in the party. One question waits per character in each (0570), and the
# join's own reads (the since-when prompt, "not yet caught up") count only the first family.
KNOWS = (JOIN, BACK)
STAYING = (LEAVE, DIED)
_KNOWS_SQL = " AND kind IN ('join', 'back')"


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
    kind: str = "join"  # (JOIN; BACK, one who left and came back, 0555; LEAVE or DIED, 0570)


_COLUMNS = "id, entity_id, status, group_id, sent_at, created_at, kind"
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
        kind=str(row.get("kind") or JOIN),
    )


async def ask(w: Changes, entity_ids: Sequence[str], kind: str = "join") -> None:
    """Ask about these characters, who joined (or, `kind` BACK, came back; LEAVE, said goodbye;
    DIED, were marked dead) in this change: one question each, one message for all of one kind
    when sent. A character with a question of the same family already waiting is not asked twice
    (the one waiting stands)."""
    if not entity_ids:
        return
    rows = [(secrets.token_hex(16), e) for e in dict.fromkeys(entity_ids)]
    cur = await w.conn.execute(
        "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id, created_at, kind)"
        " SELECT %s, %s, q.id, q.entity_id, %s, %s FROM unnest(%s::text[], %s::text[])"
        " AS q(id, entity_id) ON CONFLICT DO NOTHING RETURNING id",
        (*w.ids, w.now, kind, [r[0] for r in rows], [r[1] for r in rows]),
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
    Undo words), and a "From the first session" it was waits again (its stretch moved back).
    Refused (`TooLateToUndo`) for a join or a coming back whose own question was answered with a
    copy since: undoing it would leave a character out of the party holding the party's catch-up.
    That copy's own Undo comes first."""
    cur = await w.conn.execute(
        "SELECT 1 FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND batch = %s AND status IN ('party', 'first', 'chosen') LIMIT 1",
        (*w.ids, batch),
    )
    if await cur.fetchone() is not None:
        raise TooLateToUndo("Its question was answered with a copy since.")
    # ("Let me choose", 4c: the pick that answered can't go while a later pick stands)
    cur = await w.conn.execute(
        "SELECT 1 FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND answered_batch = %s AND status = 'chosen' AND cardinality(pick_batches) > 1"
        " LIMIT 1",
        (*w.ids, batch),
    )
    if await cur.fetchone() is not None:
        raise TooLateToUndo("A later pick stands.")
    await w.conn.execute(
        "UPDATE memory_join_questions SET pick_batches = array_remove(pick_batches, %s::bigint)"
        " WHERE guild_id = %s AND campaign_id = %s AND %s = ANY(pick_batches)",
        (batch, *w.ids, batch),
    )
    await w.conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND batch = %s AND status = 'waiting'",
        (*w.ids, batch),
    )
    await w.conn.execute(
        "UPDATE memory_join_questions SET status = CASE status WHEN 'party' THEN 'everyone'"
        " ELSE 'waiting' END, answered_batch = NULL"
        " WHERE guild_id = %s AND campaign_id = %s AND answered_batch = %s"
        # ("Left the party" undone: the goodbye or death question waits again)
        " AND status IN ('party', 'first', 'chosen', 'left')",
        (*w.ids, batch),
    )


async def withdraw(w: Changes, entity_id: str, kind: str) -> None:
    """A question of `kind` about this character that still waits goes (a death taken back by a
    later condition: Revivify, Raise Dead). One already answered stays answered: its own Undo is
    the way back (Supervisor's ruling S on #4537)."""
    await w.conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND entity_id = %s AND kind = %s AND status = 'waiting'",
        (*w.ids, entity_id, kind),
    )


async def ask_heard(
    scope: Scope, entity_id: str, speakers: Sequence[int], session_id: str, now: int
) -> bool:
    """A goodbye heard in a session (#4537 slice 6b): one `leave` question about this character,
    carrying who said it (ids only) and the session (0574), so it goes with their "delete my
    data" and with the session's transcript. A reader's question is no change to memory, so it
    rides no change (nothing to Undo; the DM's answer is the change). Not asked when a goodbye
    from this session was asked about this character before, whatever the answer ("Still in the
    party" is never asked again for the same goodbye), nor while a goodbye or death question
    about them waits (the one waiting stands). Returns whether one was asked."""
    cur = await scope.conn.execute(
        "SELECT 1 FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND entity_id = %s AND kind = 'leave' AND session_id = %s LIMIT 1",
        (*scope.ids, entity_id, session_id),
    )
    if await cur.fetchone() is not None:
        return False
    cur = await scope.conn.execute(
        "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id, created_at, kind,"
        " speaker_ids, session_id) VALUES (%s, %s, %s, %s, %s, 'leave', %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING id",
        (*scope.ids, secrets.token_hex(16), entity_id, now, sorted(set(speakers)), session_id),
    )
    return await cur.fetchone() is not None


async def forget_speaker(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Someone's words are forgotten (Stop recording me, Delete my transcripts, a DM's removal of a
    speaker; #4745's rule, 0574): a goodbye question heard in their lines that still waits goes.
    An answered one is the DM's own answer and stays, without their id. `campaign_id`: only that
    campaign's. In the caller's transaction. Returns how many questions went."""
    cur = await conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status = 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE memory_join_questions SET speaker_ids = array_remove(speaker_ids, %s::bigint)"
        " WHERE guild_id = %s AND %s = ANY(speaker_ids) AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """A session's transcript is deleted: a goodbye question heard in it that still waits goes,
    and an answered one forgets which session it came from. In the caller's transaction. Returns
    how many questions went."""
    cur = await conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND session_id = %s"
        " AND status = 'waiting'",
        (guild_id, session_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE memory_join_questions SET session_id = NULL WHERE guild_id = %s"
        " AND session_id = %s",
        (guild_id, session_id),
    )
    return gone


async def keep_recorded(
    conn: Any, guild_id: int, campaign_id: str, recorded: Collection[int]
) -> None:
    """A campaign moved to another server (`guild_id` is the new one): a waiting goodbye question
    heard from someone not recorded there goes, and an answered one keeps only the ids of those
    who are, as the language questions do. In the caller's transaction."""
    who = sorted(set(recorded))
    await conn.execute(
        "DELETE FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND status = 'waiting' AND NOT (speaker_ids <@ %s::bigint[])",
        (guild_id, campaign_id, who),
    )
    await conn.execute(
        "UPDATE memory_join_questions SET speaker_ids = ARRAY(SELECT u FROM unnest(speaker_ids)"
        " AS u WHERE u = ANY(%s::bigint[]) ORDER BY u) WHERE guild_id = %s AND campaign_id = %s"
        " AND NOT (speaker_ids <@ %s::bigint[])",
        (who, guild_id, campaign_id, who),
    )


async def claim(
    conn: Any, guild_id: int, campaign_id: str, now: int, kinds: Sequence[str] = (JOIN, BACK)
) -> list[Question]:
    """Take the questions not sent yet, to send them as one message: none while a session of the
    campaign is running (never during play). One message holds one kind (a join, a coming back,
    a goodbye or a death, each worded differently), the kind of the oldest one waiting among
    `kinds`; call again for the next. The rows are marked in the statement that reads them, so two
    callers never send the same one."""
    unsent = (
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'"
        " AND q.sent_at IS NULL AND q.kind = ANY(%s)"
        + _IN_PARTY
        + " AND NOT EXISTS (SELECT 1 FROM transcript_sessions s WHERE s.guild_id = %s"
        "  AND s.campaign_id = %s AND s.ended_at IS NULL)"
    )
    ids = (guild_id, campaign_id, list(kinds), guild_id, campaign_id)
    cur = await conn.execute(
        "SELECT q.kind FROM memory_join_questions q" + unsent + " ORDER BY q.created_at, q.id"
        " LIMIT 1",
        ids,
    )
    oldest = await cur.fetchone()
    if oldest is None:
        return []
    cur = await conn.execute(
        "UPDATE memory_join_questions q SET group_id = %s, sent_at = %s"
        + unsent
        + f" AND q.kind = %s RETURNING {_COLUMNS}",
        (secrets.token_hex(16), now, *ids, oldest["kind"]),
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


async def waiting_groups(
    conn: Any, guild_id: int, campaign_id: str, kinds: Sequence[str] = (JOIN, BACK)
) -> list[str]:
    """The sent messages of `kinds` still waiting for an answer, oldest first (❓ Questions for
    you)."""
    cur = await conn.execute(
        "SELECT q.group_id, min(q.created_at) AS at FROM memory_join_questions q"
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'"
        " AND q.group_id IS NOT NULL AND q.kind = ANY(%s)"
        + _IN_PARTY
        + " GROUP BY q.group_id ORDER BY at, q.group_id",
        (guild_id, campaign_id, list(kinds)),
    )
    return [str(r["group_id"]) for r in await cur.fetchall()]


async def waiting_for(conn: Any, guild_id: int, campaign_id: str, entity_id: str) -> bool:
    """Whether this character has a join question waiting (the since-when prompt asks only then)."""
    cur = await conn.execute(
        "SELECT 1 FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND entity_id = %s AND status = 'waiting'" + _KNOWS_SQL + " LIMIT 1",
        (guild_id, campaign_id, entity_id),
    )
    return await cur.fetchone() is not None


async def waiting_entities(conn: Any, guild_id: int, campaign_id: str) -> set[str]:
    """The characters whose own join question is still unanswered: not yet caught up, so left
    out of "what the party knows" (Supervisor's ruling on #4780)."""
    cur = await conn.execute(
        "SELECT DISTINCT entity_id FROM memory_join_questions WHERE guild_id = %s"
        " AND campaign_id = %s AND status = 'waiting'" + _KNOWS_SQL,
        (guild_id, campaign_id),
    )
    return {str(r["entity_id"]) for r in await cur.fetchall()}


async def count_waiting(
    conn: Any, guild_id: int, campaign_id: str, kinds: Sequence[str] = (JOIN, BACK)
) -> tuple[int, int | None]:
    """How many questions of `kinds` wait for the DM's answer, and since when (the board's
    count)."""
    cur = await conn.execute(
        "SELECT count(*) AS n, min(q.created_at) AS oldest FROM memory_join_questions q"
        " WHERE q.guild_id = %s AND q.campaign_id = %s AND q.status = 'waiting'"
        " AND q.kind = ANY(%s)" + _IN_PARTY,
        (guild_id, campaign_id, list(kinds)),
    )
    row = await cur.fetchone()
    if row is None:
        return 0, None
    return int(row["n"]), None if row["oldest"] is None else int(row["oldest"])


__all__ = [
    "BACK",
    "CHOSEN",
    "DIED",
    "EVERYONE",
    "FIRST",
    "JOIN",
    "KNOWS",
    "LEAVE",
    "LEFT",
    "PARTY",
    "STAYING",
    "STAYS",
    "WAITING",
    "AlreadyAnswered",
    "Question",
    "ask",
    "ask_heard",
    "claim",
    "count_waiting",
    "forget_session",
    "forget_speaker",
    "group",
    "keep_recorded",
    "set_batch",
    "unclaim",
    "undo",
    "waiting_entities",
    "waiting_for",
    "waiting_groups",
    "withdraw",
]
