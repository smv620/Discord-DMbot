"""The lore reader's story-thread suggestions, waiting for the DM (#4453, quests slice 1; design
`docs/design/quests.md` §9.1 and §9.6; Supervisor's ruling on #4453): one row a bundle, kept or
turned down as a whole from ❓ Questions for you.

A suggestion is a wait, never story memory (migration 0502): nothing in it is an entry until the DM
keeps it, so no names list, count of names, "Add all" or players' view reads it. Its one reader is
`ThreadSuggestionStore` here (CI: `test_thread_suggestion_readers`), and only the DM's card and
the upload call it. ✅ Keep goes through `MemoryStore.keep_thread`: the thread, its state and the
chosen people in one change, with this row marked kept in the same transaction (`mark_kept`), so
one Undo takes it all back and ↩ on the card puts the suggestion back (`unkeep`). 🚫 marks it
turned down; its name key stops it coming back, and so do its people (`known`). Counts only in the
log, never names."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory.models import name_key, new_id
from dmbot.memory.thread_read import Suggestion

log = logging.getLogger(__name__)

WAITING, KEPT, TURNED_DOWN = "waiting", "kept", "turned_down"
GONE = "That suggestion was already answered. Here are the ones still waiting."
UPLOAD_MAX = 200


@dataclass(frozen=True, slots=True)
class Waiting:
    """One suggestion as the DM's card shows it: entry ids only (the card names them)."""

    id: str
    kind: str
    name: str
    aim: str
    quote: str
    parts: tuple[tuple[str, str], ...]
    upload: str
    created_at: int

    @property
    def state(self) -> str:
        from dmbot.memory.thread_read import STATE

        return STATE[self.kind]


def _parts(raw: Any) -> tuple[tuple[str, str], ...]:
    rows = raw if isinstance(raw, list) else json.loads(raw or "[]")
    return tuple(
        (str(p[0]), str(p[1])) for p in rows if isinstance(p, (list, tuple)) and len(p) == 2
    )


async def mark_kept(w: Any, suggestion_id: str, batch: int | None) -> bool:
    """Inside the keep's own write (`MemoryStore.keep_thread`): the suggestion is kept, with the
    change's number. False when it was answered meanwhile (a second press, another screen)."""
    cur = await w.conn.execute(
        "UPDATE thread_suggestions SET status = 'kept', kept_batch = %s"
        " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
        (batch, w.guild_id, w.campaign_id, suggestion_id),
    )
    return bool(cur.rowcount)


class ThreadSuggestionStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def known(self, guild_id: int, campaign_id: str) -> list[tuple[str, frozenset[str]]]:
        """Every suggestion's name key and people, whatever became of it: the reader never
        suggests one again (`thread_read.check`'s `have`)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT name_key, parts FROM thread_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [(str(r["name_key"]), frozenset(e for _, e in _parts(r["parts"]))) for r in rows]

    async def offer(
        self, guild_id: int, campaign_id: str, found: Sequence[Suggestion], upload: str, now: int
    ) -> int:
        """Keep these suggestions waiting; one already made under the same name is left as it
        is (turned down stays turned down). How many are new."""
        made = 0
        async with self._db.guild(guild_id) as conn:
            for s in found:
                cur = await conn.execute(
                    "INSERT INTO thread_suggestions (guild_id, campaign_id, id, kind, name,"
                    " name_key, aim, quote, parts, upload, status, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'waiting', %s)"
                    " ON CONFLICT (guild_id, campaign_id, name_key) DO NOTHING",
                    (
                        guild_id, campaign_id, new_id(), s.kind, s.name[:120],
                        name_key(s.name)[:120] or s.kind, s.aim[:240], s.quote[:400],
                        json.dumps([list(p) for p in s.parts]), upload[:UPLOAD_MAX], max(now, 1),
                    ),
                )  # fmt: skip
                made += cur.rowcount
        if found:
            log.info("Story thread suggestions: %d new of %d", made, len(found))
        return made

    async def waiting(self, guild_id: int, campaign_id: str) -> list[Waiting]:
        """The suggestions waiting for the DM, oldest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, kind, name, aim, quote, parts, upload, created_at"
                " FROM thread_suggestions WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'waiting' ORDER BY created_at, id",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            Waiting(
                str(r["id"]), str(r["kind"]), str(r["name"]), str(r["aim"]), str(r["quote"]),
                _parts(r["parts"]), str(r["upload"]), int(r["created_at"]),
            )
            for r in rows
        ]  # fmt: skip

    async def count(self, guild_id: int, campaign_id: str) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM thread_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'waiting'",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row is not None else 0

    async def turn_down(self, guild_id: int, campaign_id: str, suggestion_id: str) -> bool:
        """🚫: never suggested again. False when it was answered meanwhile."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'turned_down'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'waiting'",
                (guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)

    async def unkeep(self, guild_id: int, campaign_id: str, batch: int) -> bool:
        """↩ Undo of a keep (the caller undid the change first): the suggestion waits again. A
        turned-down one comes back only through ↩ on its own card (`unturn`)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'waiting', kept_batch = NULL"
                " WHERE guild_id = %s AND campaign_id = %s AND kept_batch = %s"
                " AND status = 'kept'",
                (guild_id, campaign_id, batch),
            )
            return bool(cur.rowcount)

    async def unturn(self, guild_id: int, campaign_id: str, suggestion_id: str) -> bool:
        """↩ Undo of a 🚫 on the card: the suggestion waits again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE thread_suggestions SET status = 'waiting'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND status = 'turned_down'",
                (guild_id, campaign_id, suggestion_id),
            )
            return bool(cur.rowcount)
