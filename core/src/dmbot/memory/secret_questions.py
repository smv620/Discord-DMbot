"""The 🤫 questions DMbot asks instead of taking knowledge away from the players by itself (#4524,
the owner's rule; Supervisor's ruling on #4524; migration 0510).

Two kinds, one board kind so the DM has one place to look:
- **narrow:** "This sounds like a secret, but the players already know it: Keep it known / Make it
  secret". `change` is what the write would have done: column values (`{"secret": true}`) or the
  player characters it would have taken off (`{"knowers_off": [...]}`).
- **replace:** "Did this change? Keep both / End the old one", for a backstory connection that
  would end a public one (`backstory_keep.end_replaced`). `change` is `{"end": <session start>}`.

A question is asked once for each item and change (its `change_key`), whatever became of the first
(Keep it known is remembered: the ruling's point 2). It is written through the change log, so an
upload's Undo takes it back with the rest. Its evidence follows 5a's consent rule: a label, and from
a session only line starts and speakers, never words; a waiting one goes when a speaker it quotes
stops recording (`forget`). Make it secret is the DM's own change (`MemoryStore.
answer_secret_question`), one ↩ Undo. Counts only in the log."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from dmbot.memory._changes import SECRET_QUESTIONS
from dmbot.memory.models import new_id

if TYPE_CHECKING:
    from dmbot.memory._changes import Changes

log = logging.getLogger(__name__)

NARROW, REPLACE = "narrow", "replace"
WAITING, KEPT_KNOWN, MADE_SECRET = "waiting", "kept_known", "made_secret"
LABEL_MAX = 200
GONE = "That question was already answered."


def change_key(change: Mapping[str, Any]) -> str:
    """The change in a fixed form: the same change on the same item is asked once."""
    return json.dumps(change, sort_keys=True, separators=(",", ":"))[:400]


async def ask(
    w: Changes, kind: str, item_table: str, item_id: str, change: Mapping[str, Any]
) -> bool:
    """Inside a write that held something back: the question, unless this change on this item
    was asked before. Counts it held back on the write either way. True when it is new."""
    w.held_back += 1
    key = change_key(change)
    found = await w.select(
        SECRET_QUESTIONS,
        " AND item_table = %s AND item_id = %s AND change_key = %s",
        [item_table, item_id, key],
    )
    if found:
        return False
    evidence = w.evidence
    await w.insert(
        SECRET_QUESTIONS,
        {
            "id": new_id(), "kind": kind, "item_table": item_table, "item_id": item_id,
            "change": Jsonb(dict(change)), "change_key": key,
            "label": str(evidence.get("label", ""))[:LABEL_MAX],
            "session_id": evidence.get("session_id"),
            "line_starts": [int(n) for n in evidence.get("line_starts", ())],
            "speaker_ids": [int(n) for n in evidence.get("speaker_ids", ())],
            "status": WAITING, "created_at": max(w.now, 1),
        },
    )  # fmt: skip
    log.info("Public stays public: a %s question was asked instead", kind)
    return True


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
    "WAITING",
    "Question",
    "ask",
    "change_key",
    "count",
    "forget",
    "waiting",
]  # fmt: skip
