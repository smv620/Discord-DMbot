"""The bot's side of the recap page's names to check (#2113).

The website has no right on the memory tables, so a DM's answer there (it is right, it is really a
known name, fix the spelling, not a name, explain) is queued as a row (`name_answers`); this
module claims the rows and applies each through the same `MemoryStore` paths as Discord's
📝 Check new names, so the change log, Undo and every list agree, and an answer on the page
answers Discord's list too. Which names belong to which recap is `recap_name_checks`, written when
the bot queues them.

The DM's words (a spelling, a note) are about one name: they are never logged (outcome words only),
a row is deleted after an hour whatever happened, and a claimed row that stalled is picked up again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import (
    CONFIRMED,
    NAME_MAX,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    name_key,
)
from dmbot.memory.name_longer import widens
from dmbot.memory.store import SPELLING_TAKEN, MemoryStore

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this
NOTE_MAX = 300  # the note kept with a name (the same limit in the database's door and table)

# What a failed answer says, in words the page can turn into plain ones.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
GONE = "gone"  # the name was answered or removed meanwhile (here or in Discord)
BAD_KIND = "bad_kind"  # a kind the rules do not take for a name
NOT_JOINED = "not_joined"  # the merge was refused (a secret name, a player's character…)
BAD_SPELLING = "bad_spelling"  # the new spelling was refused
FAILED = "failed"  # anything unexpected; the answer can be given again
# Change the words (#2593): the codes the page turns into plain ones. None says which other entry
# is meant, so a secret name is never given away.
EMPTY = "empty"  # no words
TOO_LONG = "too_long"  # more than `NAME_MAX` letters
ALREADY_KNOWN = "already_known"  # the words are a name DMbot already knows
SECRET_CLASH = "secret_clash"  # the words are another entry's secret name (not said which)

PLAYER_CHARACTER = "player_character"  # who plays them is asked in Discord, not on the page


@dataclass(frozen=True, slots=True)
class Answer:
    id: str
    campaign_id: str
    entity_id: str
    user_id: int
    action: str
    kind: str = ""
    target_id: str = ""
    text: str = ""
    claimed_at: int = 0  # this worker's claim: only it may finish the row


class AnswerStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def link(
        self, guild_id: int, campaign_id: str, session_at: int, entity_ids: Sequence[str]
    ) -> int:
        """Say which waiting suggestions belong to this recap (ids only). Repeats change
        nothing. Returns how many were new."""
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO recap_name_checks (guild_id, campaign_id, session_at, entity_id)"
                " SELECT %s, %s, %s, e.id FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = ANY(%s)"
                " ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, session_at, guild_id, campaign_id, ids),
            )
            return int(cur.rowcount)

    async def claim_next(self, guild_id: int) -> Answer | None:
        """The oldest waiting answer (or a stalled one), marked as being worked on."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_answers SET state = 'working', claimed_at = %s WHERE (guild_id, id)"
                " = (SELECT guild_id, id FROM name_answers WHERE guild_id = %s AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, entity_id, user_id, action, kind, target_id, text",
                (now, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Answer(
            row["id"],
            row["campaign_id"],
            row["entity_id"],
            int(row["user_id"]),
            row["action"],
            row["kind"],
            row["target_id"],
            row["text"],
            now,
        )

    async def finish(self, guild_id: int, answer: Answer, *, error: str = "") -> bool:
        """Done (the row goes: the name is answered, or its note kept) or failed (with why, for the
        page). Only the worker that holds the current claim can write. False: not its row any
        more."""
        async with self._db.guild(guild_id) as conn:
            if not error:
                cur = await conn.execute(
                    "DELETE FROM name_answers WHERE guild_id = %s AND id = %s"
                    " AND state = 'working' AND claimed_at = %s",
                    (guild_id, answer.id, answer.claimed_at),
                )
            else:
                cur = await conn.execute(
                    "UPDATE name_answers SET state = 'failed', error = %s WHERE guild_id = %s"
                    " AND id = %s AND state = 'working' AND claimed_at = %s",
                    (error, guild_id, answer.id, answer.claimed_at),
                )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete answers older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM name_answers WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(
    memory: MemoryStore, guild_id: int, dm_user_ids: Collection[int], answer: Answer
) -> str:
    """Do one answer the way Discord's name check does. Returns "" when it worked, or the
    reason word. Nothing here logs the DM's words. The sender is checked at the moment of use
    (they may have stopped being a DM since); the name must still be a waiting suggestion."""
    if answer.user_id not in dm_user_ids:
        return NOT_ALLOWED
    cid = answer.campaign_id
    entity = await memory.entity(guild_id, cid, answer.entity_id)
    if entity is None or entity.status != PROPOSED:
        return GONE
    # Read again now: a name may have been made secret since the answer was queued (the door
    # checked only then), and a secret name is never answered from the page.
    if await _has_secret(memory, guild_id, cid, entity.id):
        return GONE
    try:
        if answer.action == "confirm":
            if answer.kind == PLAYER_CHARACTER:
                return BAD_KIND  # who plays them is asked in Discord
            try:
                await memory.confirm_entity(guild_id, cid, entity.id, answer.kind, source="dm")
            except MemoryRuleError:
                return BAD_KIND
        elif answer.action == "same":
            keep = await memory.entity(guild_id, cid, answer.target_id)
            # The page offers only confirmed names with nothing secret about them; a hand-made
            # request for any other (a secret one, a person at the table) is refused here too, so
            # the suggestion's spelling can never become a public name of a secret entry.
            if (
                keep is None
                or keep.status != CONFIRMED
                or keep.id == entity.id
                or keep.type == "person"
                or await _has_secret(memory, guild_id, cid, keep.id)
            ):
                return NOT_JOINED
            try:
                await memory.merge(
                    guild_id,
                    cid,
                    keep.id,
                    entity.id,
                    source="dm",
                    dm_said_same=True,
                    confirm_keys=[name_key(entity.name)],
                )
            except MemoryRuleError:
                return NOT_JOINED
        elif answer.action == "respell":
            try:
                await memory.rename_entity(guild_id, cid, entity.id, answer.text, source="dm")
            except MemoryRuleError:
                return BAD_SPELLING
        elif answer.action == "change_words":
            return await _change_words(memory, guild_id, cid, entity, answer)
        elif answer.action == "not_a_name":
            await memory.set_entity_status(guild_id, cid, entity.id, REJECTED, source="dm")
        elif answer.action == "explain":
            await memory.set_note(
                guild_id, cid, entity.id, answer.text, source="dm", limit=NOTE_MAX
            )
        else:
            return FAILED
    except MemoryRuleError:
        return GONE  # the name changed under us (answered in Discord a moment ago)
    return ""


async def _change_words(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity: Any, answer: Answer
) -> str:
    """✏️ Change the words (#2593, #2570): the whole name, and how it was heard when one of the
    longer names was picked (kept in `target_id`). The very call and checks of Discord's form: one
    change, one Undo; the card stays and the DM still says what the name is. The answer is the
    name's own, so no word of it is logged."""
    new = " ".join(answer.text.split())
    if not new:
        return EMPTY
    if len(new) > NAME_MAX:
        return TOO_LONG
    if (
        name_key(new) != name_key(entity.name)
        and await memory.confirmed_name_for(guild_id, campaign_id, name_key(new)) is not None
    ):
        return ALREADY_KNOWN
    try:
        await memory.rename_entity(
            guild_id,
            campaign_id,
            entity.id,
            new,
            source="dm",
            keep_old=not widens(entity.name, new),
            also_known_as=" ".join(answer.target_id.split()),
        )
    except MemoryRuleError as exc:
        return SECRET_CLASH if str(exc) == SPELLING_TAKEN else BAD_SPELLING
    return ""


async def _has_secret(memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str) -> bool:
    """Any secret spelling on the entry: such an entry is never listed, offered or joined from the
    page."""
    aliases = await memory.aliases(guild_id, campaign_id, entity_id=entity_id, include_secret=True)
    return any(a.secret for a in aliases)


def kinds() -> list[dict[str, Any]]:
    """The kinds a name can be said to be on the page, in the words Discord's picker uses. A
    player's character is left out: who plays them is asked in Discord."""
    from dmbot.ui.names import KINDS  # (the bot's picker: one list for both)

    return [{"key": k, "label": v} for k, v in KINDS.items() if k != PLAYER_CHARACTER]
