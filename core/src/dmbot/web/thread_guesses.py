"""A story thread's guesses on the website (#4665, the web half of quests slice 7b; migration 0539):
the theories the players voiced about a mystery, once the DM has placed them as beliefs, for a DM of
the campaign and nobody else (the door answers NULL to anyone else). The words are Discord's own
(`memory.thread_guesses.card_lines`, `guess_line`): the holder first, how sure in "is sure",
"thinks" or "doubts", never a right, wrong, warm or cold, and never a verdict beside the DM's
answer (§7). Words only; no id of a belief and no line of a transcript leaves here."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import link_words
from dmbot.memory import thread_guesses as guesses_mod
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


async def read(db: Database, session: Session, ref: str, thread: str) -> dict[str, Any]:
    """`{lines: [...]}`: Discord's card lines for this thread's guesses, newest first (one line
    for one guess, a heading and bullets for several; empty with no guess). `not_allowed` for
    anyone who is not a DM of the campaign, `not_found` for an entry that is not in it."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(thread):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_thread_guesses(%s, %s, %s) AS r", (guild_id, campaign_id, thread)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    seen = [_guess(row) for row in got.get("guesses") or []]
    return {"lines": guesses_mod.card_lines(seen)}


def _guess(row: dict[str, Any]) -> guesses_mod.Guess:
    """One door row as Discord's `Guess`: the words are the connection's own, as the store makes
    them (`MemoryStore.thread_guesses`)."""
    said = str(row["detail"] or "") or link_words.connection_words(str(row["predicate"]))
    return guesses_mod.Guess(
        str(row["id"]),
        str(row["holder"] or ""),
        f"{row['subject'] or '?'} {said} {row['object'] or '?'}",
        float(row["confidence"]),
        bool(row["accusation"]),
        int(row["seq"]),
    )
