"""The website's lineage or class answer ("Yes, change it" / "No, leave it"), applied by the bot
(#3946; the web half of #3926). The page queues a row in `trait_answers` (ids, the question's key
and the answer, never the trait's words or the quote); this round claims the oldest, checks again
that the sender is still a DM of the campaign and that the question is still waiting, then does
exactly what the Discord card's buttons do (`TraitOfferStore.keep` or `never`), so the two places
can never disagree and a question is answered once. A Yes writes the change as the DM's own and
its batch goes on the row, for the page's Undo. It ends `done`, or `failed` with a short word (at
most 40 characters) the page turns into plain words. Counts only in the log.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.logs import log_context
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.trait_offers import NotWaiting, Offer, TraitOfferStore

log = logging.getLogger(__name__)

GIVE_UP_S = 30 * 60  # as the door's: an answer older than this was taken as lost
RETAKE_AFTER_S = 10 * 60  # a claim that stopped moving this long ago (the bot restarted) is retaken
MAX_TRIES = 3  # an answer that keeps stopping the bot is failed, not retaken for ever

# The words a failed answer ends with (the page turns them into plain words). "gone" is a
# question no longer waiting (answered in Discord, or its character is gone); "refused" is a Yes
# the rules link refused (remembered as "never"); "failed" is anything else. The door also writes
# "lost" itself.
NOT_ALLOWED, GONE, REFUSED, FAILED = "not-allowed", "gone", "refused", "failed"
WORDS = (NOT_ALLOWED, GONE, REFUSED, FAILED)


@dataclass(slots=True)
class Answer:
    id: str
    campaign_id: str
    user_id: int
    entity_id: str
    kind: str
    name_key: str
    action: str
    claimed_at: int  # milliseconds: the token that lets only this worker write
    tries: int = 1


class TraitAnswers:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Answer | None:
        """The oldest waiting answer (or one that stopped moving), marked as being worked on."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE trait_answers SET state = 'working', claimed_at = %s, tries = tries + 1"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM trait_answers"
                " WHERE guild_id = %s AND created_at >= %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, entity_id, kind, name_key, action, tries",
                (now_ms, guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Answer(
            str(row["id"]), str(row["campaign_id"]), int(row["user_id"]), str(row["entity_id"]),
            str(row["kind"]), str(row["name_key"]), str(row["action"]), now_ms, int(row["tries"]),
        )  # fmt: skip

    async def end(
        self, guild_id: int, answer: Answer, *, ok: bool, error: str = "", batch: int | None = None
    ) -> bool:
        """Close the answer, only if this worker still holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE trait_answers SET state = %s, error = %s, batch = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                ("done" if ok else "failed", "" if ok else error[:40], batch, guild_id,
                 answer.id, answer.claimed_at),
            )  # fmt: skip
            return bool(cur.rowcount)


async def run_round(bot: Any, guild_id: int) -> int:
    """Apply one waiting answer on a server; returns how many were taken (0 or 1)."""
    if bot.memory is None:
        return 0
    answers = TraitAnswers(bot.campaigns.db)
    answer = await answers.claim_next(guild_id)
    if answer is None:
        return 0
    with log_context(guild_id=guild_id, campaign_id=answer.campaign_id):
        batch: int | None = None
        try:
            if answer.tries > MAX_TRIES:
                word = FAILED
            else:
                word, batch = await _apply(bot, guild_id, answer)
        except Exception as exc:
            log.warning("A website lineage answer failed (%s)", type(exc).__name__)
            word = FAILED
        try:
            held = await answers.end(guild_id, answer, ok=not word, error=word, batch=batch)
        except Exception as exc:  # the row stays working and is retaken, then failed at MAX_TRIES
            log.warning("A website lineage answer could not be closed (%s)", type(exc).__name__)
            return 1
        log.info(
            "A website lineage answer ended: %s %s%s",
            answer.action, word or "done", "" if held else " (lost)",
        )  # fmt: skip
    return 1


async def _apply(bot: Any, guild_id: int, answer: Answer) -> tuple[str, int | None]:
    """The word the answer ends with ("" for done) and the Yes's batch. Every check is made again
    here: a minute may have passed since the page queued it."""
    campaign = await bot.campaigns.get(guild_id, answer.campaign_id)
    if campaign is None:
        return GONE, None
    if not campaign.is_dm(answer.user_id):  # still a DM of this campaign
        return NOT_ALLOWED, None
    store = TraitOfferStore(bot.memory._db)
    offer = _find(await store.waiting(guild_id, answer.campaign_id), answer)
    if offer is None:
        return GONE, None  # answered in Discord meanwhile, or the character is gone
    try:
        if answer.action == "keep":
            return "", await store.keep(bot.memory, guild_id, answer.campaign_id, offer)
        if not await store.never(guild_id, answer.campaign_id, offer):
            return GONE, None
    except NotWaiting:  # (another press got there between the look and the write)
        return GONE, None
    except MemoryRuleError:
        return REFUSED, None
    return "", None


def _find(waiting: list[Offer], answer: Answer) -> Offer | None:
    """The waiting question the answer is for. Pure."""
    for offer in waiting:
        if (offer.entity_id, offer.trait.kind, offer.key) == (
            answer.entity_id, answer.kind, answer.name_key
        ):  # fmt: skip
            return offer
    return None
