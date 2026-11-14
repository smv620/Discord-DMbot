"""The "Does Claude still have it?" questions on the website (#4122, the web half of #4115 slice 1).

Two jobs, both the bot's (the page never writes the story itself):

- **The list** (`refresh`): the questions `MemoryStore.hand_over_waiting` gives, written into
  `hand_over_questions` for the page's door (0424). The rule lives in `memory/hand_over.py` alone,
  so Discord and the website cannot disagree. Ids and a time only; written again every minute
  and after every answer, keeping when each question was first listed.
- **The answers** (`run_round`): the page queues a row in `hand_over_answers` (`no`, `both`, or
  `undo` of an earlier `no`). This round claims the oldest, checks again that the sender is still a
  DM and that the question still waits, then does exactly what the Discord card's buttons do
  (`answer_hand_over`, `undo`). A `no` writes the change as the DM's own and its batch goes on the
  row, for the page's Undo. It ends `done`, or `failed` with a short word the page turns into
  plain words. Counts only in the log.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.logs import log_context
from dmbot.memory.hand_over import Question
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo

log = logging.getLogger(__name__)

GIVE_UP_S = 30 * 60  # as the door's: an answer older than this was taken as lost
RETAKE_AFTER_S = 10 * 60  # a claim that stopped moving this long ago (the bot restarted) is retaken
MAX_TRIES = 3  # an answer that keeps stopping the bot is failed, not retaken for ever

# The words a failed answer ends with. "gone" is a question no longer waiting (answered in Discord,
# a tie changed); "too-late" is an Undo past the days Undo works for; "changed" an Undo something
# done since stands in the way of; "failed" is anything else. The door also writes "lost" itself.
NOT_ALLOWED, GONE, TOO_LATE, CHANGED, FAILED = (
    "not-allowed",
    "gone",
    "too-late",
    "changed",
    "failed",
)
WORDS = (NOT_ALLOWED, GONE, TOO_LATE, CHANGED, FAILED)


async def refresh(db: Database, guild_id: int, campaign_id: str, questions: list[Question]) -> int:
    """Write a campaign's waiting questions for the page, in the order Discord asks them: a question
    already listed keeps when it was first listed (`made_at`, "waiting since"), one no longer
    waiting goes. Returns how many."""
    now = int(time.time())
    keys = [f"{q.older_relation_id}:{q.newer_relation_id}" for q in questions]
    async with db.guild(guild_id) as conn:
        await conn.execute(
            "DELETE FROM hand_over_questions WHERE guild_id = %s AND campaign_id = %s"
            " AND older_relation_id || ':' || newer_relation_id <> ALL(%s::text[])",
            (guild_id, campaign_id, keys),
        )
        for place, q in enumerate(questions):
            await conn.execute(
                "INSERT INTO hand_over_questions (guild_id, campaign_id, item_id, older_id,"
                " newer_id, predicate, older_relation_id, newer_relation_id, place, made_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, older_relation_id, newer_relation_id)"
                " DO UPDATE SET place = EXCLUDED.place",
                (guild_id, campaign_id, q.item_id, q.older_id, q.newer_id, q.predicate,
                 q.older_relation_id, q.newer_relation_id, place, now),
            )  # fmt: skip
    return len(questions)


async def refresh_guild(bot: Any, guild_id: int) -> int:
    """Rewrite the list for every campaign on a server; one campaign's trouble never stops the
    others. Returns how many questions wait in all."""
    if bot.memory is None:  # (campaign memory is off: there is nothing to ask)
        return 0
    total = 0
    for campaign in await bot.campaigns.list_campaigns(guild_id):
        total += await refresh_campaign(bot, guild_id, campaign.id)
    return total


# There is no heartbeat as `script_make` has: applying an answer is a few quick writes, far inside
# RETAKE_AFTER_S, so a claim that stops moving really was lost.
@dataclass(slots=True)
class Answer:
    id: str
    campaign_id: str
    user_id: int
    older_relation_id: str
    newer_relation_id: str
    action: str
    undoes: str | None
    claimed_at: int  # milliseconds: the token that lets only this worker write
    tries: int = 1


class HandOverAnswers:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Answer | None:
        """The oldest waiting answer (or one that stopped moving), marked as being worked on."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE hand_over_answers SET state = 'working', claimed_at = %s,"
                " tries = tries + 1 WHERE (guild_id, id) = (SELECT guild_id, id"
                " FROM hand_over_answers WHERE guild_id = %s AND created_at >= %s"
                " AND (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, older_relation_id, newer_relation_id,"
                " action, undoes, tries",
                (now_ms, guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Answer(
            str(row["id"]), str(row["campaign_id"]), int(row["user_id"]),
            str(row["older_relation_id"]), str(row["newer_relation_id"]), str(row["action"]),
            row["undoes"], now_ms, int(row["tries"]),
        )  # fmt: skip

    async def end(
        self, guild_id: int, answer: Answer, *, ok: bool, error: str = "", batch: int | None = None
    ) -> bool:
        """Close the answer, only if this worker still holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE hand_over_answers SET state = %s, error = %s, batch = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                ("done" if ok else "failed", "" if ok else error[:40], batch, guild_id,
                 answer.id, answer.claimed_at),
            )  # fmt: skip
            return bool(cur.rowcount)

    async def spent(self, guild_id: int, campaign_id: str, answer_id: str) -> None:
        """A `no` that was undone: its change is gone, so it offers Undo no more (the doors read
        an undoable `no` by its batch)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE hand_over_answers SET batch = NULL WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND action = 'no'",
                (guild_id, campaign_id, answer_id),
            )

    async def batch_of(self, guild_id: int, campaign_id: str, answer_id: str) -> int | None:
        """The change an earlier, done `no` made (what an `undo` takes back), or None."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT batch FROM hand_over_answers WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND action = 'no' AND state = 'done'",
                (guild_id, campaign_id, answer_id),
            )
            row = await cur.fetchone()
        return None if row is None or row["batch"] is None else int(row["batch"])


async def run_round(bot: Any, guild_id: int) -> int:
    """Apply one waiting answer on a server; returns how many were taken (0 or 1)."""
    if bot.memory is None:
        return 0
    answers = HandOverAnswers(bot.campaigns.db)
    answer = await answers.claim_next(guild_id)
    if answer is None:
        return 0
    with log_context(guild_id=guild_id, campaign_id=answer.campaign_id):
        batch: int | None = None
        try:
            if answer.tries > MAX_TRIES:
                word = FAILED
            else:
                word, batch = await _apply(bot, answers, guild_id, answer)
        except Exception as exc:
            log.warning("A website hand-over answer failed (%s)", type(exc).__name__)
            word = FAILED
        try:
            held = await answers.end(guild_id, answer, ok=not word, error=word, batch=batch)
        except Exception as exc:  # the row stays working and is retaken, then failed at MAX_TRIES
            log.warning("A website hand-over answer could not be closed (%s)", type(exc).__name__)
            return 1
        log.info(
            "A website hand-over answer ended: %s %s%s",
            answer.action, word or "done", "" if held else " (lost)",
        )  # fmt: skip
        if not word and answer.action == "undo" and held:
            # (the `no` it took back offers no Undo any more, and a second one is refused)
            await answers.spent(guild_id, answer.campaign_id, answer.undoes or "")
        if word in ("", GONE):  # (the list follows at once, not a minute later)
            await refresh_campaign(bot, guild_id, answer.campaign_id)
    return 1


async def refresh_campaign(bot: Any, guild_id: int, campaign_id: str) -> int:
    """One campaign's list, again; a failure is logged (0) and the next round writes it."""
    try:
        questions = await bot.memory.hand_over_waiting(guild_id, campaign_id, source=DM)
        return await refresh(bot.campaigns.db, guild_id, campaign_id, questions)
    except Exception as exc:
        log.warning("The hand-over questions could not be listed (%s)", type(exc).__name__)
        return 0


async def _apply(
    bot: Any, answers: HandOverAnswers, guild_id: int, answer: Answer
) -> tuple[str, int | None]:
    """The word the answer ends with ("" for done) and a `no`'s batch. Every check is made again
    here: a minute may have passed since the page queued it."""
    campaign = await bot.campaigns.get(guild_id, answer.campaign_id)
    if campaign is None:
        return GONE, None
    if not campaign.is_dm(answer.user_id):  # still a DM of this campaign
        return NOT_ALLOWED, None
    if answer.action == "undo":
        undo = await answers.batch_of(guild_id, answer.campaign_id, answer.undoes or "")
        if undo is None:
            return GONE, None
        try:
            await bot.memory.undo(guild_id, answer.campaign_id, undo, source=DM)
        except TooLateToUndo:
            return TOO_LATE, None
        except MemoryRuleError:
            if answer.tries > 1 and await _tie_open(bot, guild_id, answer):
                return "", None  # a retaken Undo whose write landed before the close failed
            return CHANGED, None
        return "", None
    waiting = await bot.memory.hand_over_waiting(guild_id, answer.campaign_id, source=DM)
    question = _find(waiting, answer)
    if question is None:
        if answer.tries > 1 and await _already_done(bot, guild_id, answer):
            # a retaken answer whose write landed before the close failed: done, never "gone"
            # (a `no`'s change is then not offered for Undo: its batch was not kept)
            return "", None
        return GONE, None  # answered in Discord meanwhile, or a tie changed
    try:
        written = await bot.memory.answer_hand_over(
            guild_id, answer.campaign_id, question, still_has=answer.action == "both", source=DM
        )
    except MemoryRuleError:  # (another press got there between the look and the write)
        return GONE, None
    return "", written.batch if answer.action == "no" else None


async def _tie(bot: Any, guild_id: int, answer: Answer) -> Any | None:
    """The older holder's tie as it is now, or None."""
    rels = await bot.memory.relations(guild_id, answer.campaign_id, include_secret=True)
    return next((r for r in rels if r.id == answer.older_relation_id), None)


async def _tie_open(bot: Any, guild_id: int, answer: Answer) -> bool:
    tie = await _tie(bot, guild_id, answer)
    return tie is not None and tie.to_session_at is None


async def _already_done(bot: Any, guild_id: int, answer: Answer) -> bool:
    """Whether what the answer asks for is already so: a `no`'s tie ended, or a `both` remembered
    for the pair (the bot stopped between the write and closing the row)."""
    if answer.action == "no":
        tie = await _tie(bot, guild_id, answer)
        return tie is not None and tie.to_session_at is not None
    newer = await bot.memory.relations(guild_id, answer.campaign_id, include_secret=True)
    pair = {r.subject_id for r in newer if r.id in (answer.older_relation_id,
                                                     answer.newer_relation_id)}  # fmt: skip
    item = {r.object_id for r in newer if r.id == answer.newer_relation_id}
    if len(pair) != 2 or len(item) != 1:
        return False
    first, second = sorted(pair)
    async with bot.campaigns.db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM memory_hand_over_both WHERE guild_id = %s AND campaign_id = %s"
            " AND item_id = %s AND first_id = %s AND second_id = %s",
            (guild_id, answer.campaign_id, next(iter(item)), first, second),
        )
        return await cur.fetchone() is not None


def _find(waiting: list[Question], answer: Answer) -> Question | None:
    """The waiting question the answer is for, by its two ties. Pure."""
    for q in waiting:
        if (q.older_relation_id, q.newer_relation_id) == (
            answer.older_relation_id, answer.newer_relation_id
        ):  # fmt: skip
            return q
    return None
