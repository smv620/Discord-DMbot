"""The "Did they mean…?" questions nobody answered during a session (#1572; docs/PLAN.md,
"Transcript Cleaner"), kept for the after-session check so the DM can still say that "Marin"
was Maren. The bot advises; the DM decides: nothing changes until the DM answers.

This file is the rules (pure) and the table (`WaitingQuestionStore`). The screen is in
`dmbot.ui.heard_review`; collecting them while a session runs and applying an answer is in the
bot (`save_unanswered`, `answer_waiting_question`).

- At most `MAX_PER_SESSION` are kept for a session; the rest are dropped quietly, oldest first
  (as an unanswered live question is).
- Nothing is kept for a speaker who has stopped being recorded, and a person who stops has their
  waiting questions removed with their other traces (`forget_speaker`).
- A question that waits more than `EXPIRE_S` is not asked any more, with no message.
- Only the line's reference is stored (session, speaker, when it started), never its words: the
  line is read again when the question is shown, and a line that has been deleted since takes
  its questions out of the list.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.transcript.cleaner import with_ending

MAX_PER_SESSION = 25
EXPIRE_S = 7 * 24 * 60 * 60  # an unanswered one expires quietly after a week
OPEN, ANSWERED, KEPT, EXPIRED = "open", "answered", "kept", "expired"
ANSWER_NOT_SAVED = "Couldn't save that. Nothing was changed. Press it again."


@dataclass(frozen=True, slots=True)
class Unanswered:
    """A question to keep, as the running session had it: whose line, when it started, the
    misheard words and the entries they might be."""

    speaker_id: int
    started_ms: int
    heard: str
    option_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Waiting:
    """A kept question, with its line as it reads now."""

    id: int
    campaign_id: str
    session_id: str
    speaker_id: int
    started_ms: int
    heard: str
    option_ids: tuple[str, ...]
    text: str  # the line's cleaned words now


def _find(text: str, heard: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"(?<!\w){re.escape(heard)}(?!\w)", text, re.IGNORECASE))


def find_once(text: str, heard: str) -> tuple[int, int] | None:
    """Where the misheard words are in the line, if they are there exactly once (whole words, any
    case); None when the line was changed since, or the words come twice and DMbot can't tell
    which was meant."""
    found = _find(text, heard)
    return (found[0].start(), found[0].end()) if len(found) == 1 else None


def fix_words(text: str, heard: str, name: str) -> str | None:
    """`text` with the misheard words written as `name`, or None when `find_once` finds no single
    place. A "'s" on the words stays on the name."""
    span = find_once(text, heard)
    if span is None:
        return None
    start, end = span
    return text[:start] + with_ending(text[start:end], name) + text[end:]


_COLUMNS = "id, campaign_id, session_id, speaker_id, started_ms, heard, option_ids"


def _row(r: dict[str, Any], text: str = "") -> Waiting:
    return Waiting(
        int(r["id"]),
        str(r["campaign_id"]),
        str(r["session_id"]),
        int(r["speaker_id"]),
        int(r["started_ms"]),
        str(r["heard"]),
        tuple(str(o) for o in r["option_ids"]),
        text,
    )


class WaitingQuestionStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def save(
        self, guild_id: int, campaign_id: str, session_id: str, items: Sequence[Unanswered]
    ) -> int:
        """Keep the questions the session left unanswered, oldest first in `items`: the newest
        `MAX_PER_SESSION` stay (the rest are dropped quietly). A word in a line is kept once.
        How many were saved."""
        kept = list(items)[-MAX_PER_SESSION:]
        saved = 0
        async with self._db.guild(guild_id) as conn:
            for item in kept:
                cur = await conn.execute(
                    "INSERT INTO waiting_name_questions"
                    " (guild_id, campaign_id, session_id, speaker_id, started_ms, heard,"
                    "  option_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (
                        guild_id,
                        campaign_id,
                        session_id,
                        item.speaker_id,
                        item.started_ms,
                        item.heard,
                        list(item.option_ids),
                        int(self._now()),
                    ),
                )
                saved += cur.rowcount
        return saved

    async def expire_old(self, guild_id: int, campaign_id: str) -> int:
        """Open questions older than a week stop being asked (no message)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE waiting_name_questions SET state = 'expired', decided_at = %s"
                " WHERE campaign_id = %s AND state = 'open' AND created_at < %s",
                (int(self._now()), campaign_id, int(self._now()) - EXPIRE_S),
            )
            return cur.rowcount

    async def waiting(self, guild_id: int, campaign_id: str, *, limit: int = 100) -> list[Waiting]:
        """The campaign's open questions, oldest first, each with its line's words now. One whose
        line is gone is left out."""
        await self.expire_old(guild_id, campaign_id)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT q.id, q.campaign_id, q.session_id, q.speaker_id, q.started_ms, q.heard,"
                " q.option_ids, coalesce(l.text, l.heard) AS text"
                " FROM waiting_name_questions q CROSS JOIN LATERAL ("
                "  SELECT heard, text FROM transcript_lines l"
                "  WHERE l.session_id = q.session_id AND l.user_id = q.speaker_id"
                "  AND l.started_ms = q.started_ms AND l.sidebar IS NULL AND NOT l.typed"
                "  ORDER BY l.id LIMIT 1) l"
                " WHERE q.campaign_id = %s AND q.state = 'open'"
                " ORDER BY q.created_at, q.id LIMIT %s",
                (campaign_id, limit),
            )
            rows = await cur.fetchall()
        return [_row(r, str(r["text"])) for r in rows]

    async def count(self, guild_id: int, campaign_id: str) -> int:
        return len(await self.waiting(guild_id, campaign_id))

    async def count_for_session(self, guild_id: int, session_id: str) -> int:
        """How many of this session's questions are still open (the recap waits for them, #2169).
        Counted in the database, not read line by line: a count is all that is asked. A question
        left a week expires quietly (EXPIRE_S) and stops counting, so it may release a held
        recap: the DM is never kept waiting on a question the bot itself has dropped (owner
        ruling via Supervisor, #2173)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM waiting_name_questions"
                " WHERE session_id = %s AND state = %s AND created_at > %s",
                (session_id, OPEN, int(self._now()) - EXPIRE_S),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def get(self, guild_id: int, question_id: int) -> Waiting | None:
        """One open question with its line as it reads now, or None (answered, expired, gone)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT q.id, q.campaign_id, q.session_id, q.speaker_id, q.started_ms, q.heard,"
                " q.option_ids, coalesce(l.text, l.heard) AS text"
                " FROM waiting_name_questions q CROSS JOIN LATERAL ("
                "  SELECT heard, text FROM transcript_lines l"
                "  WHERE l.session_id = q.session_id AND l.user_id = q.speaker_id"
                "  AND l.started_ms = q.started_ms AND l.sidebar IS NULL AND NOT l.typed"
                "  ORDER BY l.id LIMIT 1) l"
                " WHERE q.id = %s AND q.state = 'open' AND q.created_at >= %s",
                (question_id, int(self._now()) - EXPIRE_S),
            )
            row = await cur.fetchone()
        return None if row is None else _row(row, str(row["text"]))

    async def decide(self, guild_id: int, question_id: int, state: str, by: int) -> bool:
        """The DM's answer (answered or kept). True only for the call that closed it: a second
        press changes nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE waiting_name_questions SET state = %s, decided_at = %s, decided_by = %s"
                " WHERE id = %s AND state = 'open'",
                (state, int(self._now()), by, question_id),
            )
            return cur.rowcount == 1

    async def reopen(self, guild_id: int, question_id: int) -> None:
        """An answer that couldn't be saved goes back to waiting, so the DM can try again."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE waiting_name_questions SET state = 'open', decided_at = NULL,"
                " decided_by = NULL WHERE id = %s AND state IN ('answered', 'kept')",
                (question_id,),
            )

    async def forget_speaker(self, guild_id: int, user_id: int) -> int:
        """Stop recording me: this person's waiting questions go, in every campaign of the
        server."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM waiting_name_questions WHERE guild_id = %s AND speaker_id = %s",
                (guild_id, user_id),
            )
            return cur.rowcount
