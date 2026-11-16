"""The 🤫 questions DMbot asks instead of taking knowledge away from the players by itself (#4524,
the owner's rule; Supervisor's ruling on #4524; migration 0510).

Two kinds, one board kind so the DM has one place to look:
- **narrow:** "This sounds like a secret, but the players already know it: Keep it known / Make it
  secret". `change` is what the write would have done: column values (`{"secret": true}`) or the
  player characters it would have taken off (`{"knowers_off": [...]}`).
- **replace:** "Did this change? Keep both / End the old one", for a backstory connection that
  would end a public one (`backstory_keep.end_replaced`), asked once the new one is kept. `change`
  is `{"end": true, "by": <the new connection>, "at": <when the old one would end>}`.

- **reveal:** "Your players knew this name, but it is also a secret name of <entry>: Keep it
  secret / Let them know", when a merge keeps the DM's own secret name though the players knew it
  on the entry that went (Supervisor's review of #4545). The secret stays until the DM answers; the
  who-knows rows that came with the folded name sleep under the question's id meanwhile, so no
  player character sees it on the kept entry. `change` is `{"secret": false}`.

A question is asked once for each item and change (its `change_key`, a hash without "at"),
whatever became of the first (Keep it known is remembered: the ruling's point 2). It is not in the
change log: it carries the asking write's batch, and an Undo of that batch takes back the ones
still waiting (`MemoryStore.undo`). Its evidence follows 5a's consent rule: a label, and from a
session only line starts and speakers, never words (set by the write, `Changes.evidence`, or by
the caller around a run, `evidence`); a waiting one goes when a speaker it quotes stops recording
(`forget`). Make it secret is the DM's own change through the store's own buttons
(`MemoryStore.answer_secret_question`), one ↩ Undo. Counts only in the log."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from dmbot.memory._changes import SECRET_QUESTIONS
from dmbot.memory.models import new_id

if TYPE_CHECKING:
    from dmbot.memory._changes import Changes

log = logging.getLogger(__name__)

NARROW, REPLACE, REVEAL = "narrow", "replace", "reveal"
WAITING, KEPT_KNOWN, MADE_SECRET = "waiting", "kept_known", "made_secret"
LABEL_MAX = 200
GONE = "That question was already answered."


# The evidence a run's questions carry when its writes don't set their own (`Changes.evidence`):
# an upload's label, or a session's label, line starts and speakers (never words).
_EVIDENCE: contextvars.ContextVar[Mapping[str, Any] | None] = contextvars.ContextVar(
    "secret_question_evidence", default=None
)


@contextlib.contextmanager
def evidence(
    label: str,
    *,
    session_id: str | None = None,
    line_starts: Sequence[int] = (),
    speaker_ids: Sequence[int] = (),
) -> Iterator[None]:
    """Every 🤫 question asked inside this block cites this evidence (an upload's run, a session's
    read). From a session only line starts and speakers: 5a's consent rule."""
    token = _EVIDENCE.set(
        {"label": label, "session_id": session_id, "line_starts": list(line_starts),
         "speaker_ids": list(speaker_ids)}
    )  # fmt: skip
    try:
        yield
    finally:
        _EVIDENCE.reset(token)


def change_key(change: Mapping[str, Any]) -> str:
    """The change in a fixed form, hashed: the same change on the same item is asked once. "at" (a
    time) is left out, so a later upload asking to end the same connection for the same new one is
    the same question."""
    fixed = json.dumps(
        {k: v for k, v in change.items() if k != "at"}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(fixed.encode()).hexdigest()


async def ask(
    w: Changes, kind: str, item_table: str, item_id: str, change: Mapping[str, Any]
) -> bool:
    """Inside a write that held something back: the question, unless this change on this item
    was asked before. True when it is new, which is also counted on the write (`held_back`)."""
    key = change_key(change)
    found = await w.select(
        SECRET_QUESTIONS,
        " AND item_table = %s AND item_id = %s AND change_key = %s",
        [item_table, item_id, key],
    )
    if found:
        return False
    proof = w.evidence or _EVIDENCE.get() or {}
    question_id = new_id()
    cur = await w.conn.execute(
        "INSERT INTO secret_questions (guild_id, campaign_id, id, kind, item_table, item_id,"
        " change, change_key, label, session_id, line_starts, speaker_ids, status, created_at,"
        " batch) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'waiting', %s, %s)"
        " ON CONFLICT DO NOTHING",
        (
            *w.ids, question_id, kind, item_table, item_id, Jsonb(dict(change)), key,
            str(proof.get("label") or "")[:LABEL_MAX], proof.get("session_id"),
            [int(n) for n in proof.get("line_starts") or ()],
            [int(n) for n in proof.get("speaker_ids") or ()],
            max(w.now, 1), w.batch,
        ),
    )  # fmt: skip
    if not cur.rowcount:
        return False
    w.held_back += 1
    w.asked.append(question_id)
    log.info("Public stays public: a %s question was asked instead", kind)
    return True


async def ask_reveal(w: Changes, alias_id: str) -> str:
    """A merge kept the DM's secret name `alias_id` though the players knew it on the entry that
    went: the "Keep it secret / Let them know" question (`REVEAL`). Asked once; returns its id
    (the earlier one's when it was asked before), which the carried who-knows rows sleep under."""
    change = {"secret": False}
    if await ask(w, REVEAL, "memory_aliases", alias_id, change):
        return w.asked[-1]
    cur = await w.conn.execute(
        "SELECT id FROM secret_questions WHERE guild_id = %s AND campaign_id = %s"
        " AND item_table = 'memory_aliases' AND item_id = %s AND change_key = %s",
        (*w.ids, alias_id, change_key(change)),
    )
    row = await cur.fetchone()
    return str(row["id"]) if row is not None else ""


@dataclass(frozen=True, slots=True)
class Question:
    """One waiting question, ids only: the card names the item when it is drawn."""

    id: str
    kind: str
    item_table: str
    item_id: str
    change: dict[str, Any]
    label: str
    created_at: int


def _question(r: Mapping[str, Any]) -> Question:
    change = r["change"] if isinstance(r["change"], dict) else json.loads(r["change"] or "{}")
    return Question(
        str(r["id"]), str(r["kind"]), str(r["item_table"]), str(r["item_id"]), dict(change),
        str(r["label"]), int(r["created_at"]),
    )  # fmt: skip


async def waiting(conn: Any, guild_id: int, campaign_id: str) -> list[Question]:
    """The questions waiting for the DM, oldest first."""
    cur = await conn.execute(
        "SELECT id, kind, item_table, item_id, change, label, created_at FROM secret_questions"
        " WHERE guild_id = %s AND campaign_id = %s AND status = 'waiting'"
        " ORDER BY created_at, id",
        (guild_id, campaign_id),
    )
    return [_question(r) for r in await cur.fetchall()]


async def count(conn: Any, guild_id: int, campaign_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) AS n FROM secret_questions"
        " WHERE guild_id = %s AND campaign_id = %s AND status = 'waiting'",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row is not None else 0


async def undo(w: Changes, batch: int) -> None:
    """An Undo of `batch` (inside its write): the questions it asked that still wait go with it,
    and a Make it secret it was is waiting again. Keep it known stays remembered."""
    await w.conn.execute(
        "DELETE FROM secret_questions WHERE guild_id = %s AND campaign_id = %s AND batch = %s"
        " AND status = 'waiting'",
        (*w.ids, batch),
    )
    await w.conn.execute(
        "UPDATE secret_questions SET status = 'waiting', answered_batch = NULL"
        " WHERE guild_id = %s AND campaign_id = %s AND answered_batch = %s",
        (*w.ids, batch),
    )


async def forget(conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None) -> int:
    """Stop recording me, "Delete my transcripts" and a DM's removal of a speaker: the waiting
    questions that quote this person go (5a's consent rule); an answered one is the DM's and stays,
    without this person's id and its lines. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM secret_questions WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status = 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE secret_questions SET speaker_ids = array_remove(speaker_ids, %s),"
        " line_starts = '{}' WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


__all__ = [
    "GONE",
    "KEPT_KNOWN",
    "MADE_SECRET",
    "NARROW",
    "REPLACE",
    "REVEAL",
    "WAITING",
    "Question",
    "ask",
    "ask_reveal",
    "change_key",
    "count",
    "evidence",
    "forget",
    "undo",
    "waiting",
]  # fmt: skip
