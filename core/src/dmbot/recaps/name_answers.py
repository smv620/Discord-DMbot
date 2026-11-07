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
from dmbot.memory._changes import ALREADY_UNDONE, NOT_FOUND
from dmbot.memory.models import (
    CONFIRMED,
    NAME_MAX,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    lookup_key,
    name_key,
)
from dmbot.memory.name_longer import widens
from dmbot.memory.store import NOT_A_LIST, SPELLING_TAKEN, MemoryStore

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

# Split a list, say it is a group, and take that back (#3406, #3383): the words the page turns into
# plain ones. A refusal changes nothing.
NOT_A_LIST_WORD = "not_a_list"  # the name is not a list of names (or a list of places or things)
NOT_FOUND_WORD = "not_found"  # the group is not a confirmed one, or the answer to undo is not here
SPELLING_TAKEN_WORD = "spelling_taken"  # the group's name is another entry's spelling
ANSWERED = "answered"  # undo, after a part was answered or the names changed
NOT_DM = "not_dm"  # the sender is not a DM of the campaign any more
SPLIT, JOIN_GROUP, MAKE_GROUP, UNDO = "split", "join_group", "make_group", "undo"
KEPT_ACTIONS = (SPLIT, JOIN_GROUP, MAKE_GROUP)  # a finished one stays as a `done` row for its Undo
# DMbot's own matches (#3602, slice 4b): the DM says a name DMbot matched on its own is right, or
# takes it back. The answer's target is the match's batch (digits); its entity is the kept entry.
CONFIRM_MATCH, UNDO_MATCH = "confirm_match", "undo_match"
MATCH_ACTIONS = (CONFIRM_MATCH, UNDO_MATCH)
NEW_ACTIONS = (*KEPT_ACTIONS, UNDO, *MATCH_ACTIONS)

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


@dataclass(slots=True)
class Kept:
    """What a finished split or group answer keeps on its row for the page and for its Undo: the
    store's batch, the list as it was (a split only) and a note in counts and the group's name."""

    batch: int | None = None
    undo_name: str | None = None
    note: str = ""


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

    async def link_like(
        self, guild_id: int, campaign_id: str, from_entity: str, entity_ids: Sequence[str]
    ) -> int:
        """The new names a split or group made belong to the same recaps as the list they came
        from (#3406), so the page's list shows them. Ids only; repeats change nothing."""
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO recap_name_checks (guild_id, campaign_id, session_at, entity_id)"
                " SELECT k.guild_id, k.campaign_id, k.session_at, e.id"
                " FROM recap_name_checks k, memory_entities e"
                " WHERE k.guild_id = %s AND k.campaign_id = %s AND k.entity_id = %s"
                " AND e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id"
                " AND e.id = ANY(%s) ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, from_entity, ids),
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

    async def finish(
        self, guild_id: int, answer: Answer, *, error: str = "", kept: Kept | None = None
    ) -> bool:
        """Done (the row goes: the name is answered, or its note kept) or failed (with why, for the
        page). `kept`: a split or group answer stays as a `done` row with its batch and note until
        its Undo or the sweep (#3406). Only the worker that holds the current claim can write.
        False: not its row any more."""
        async with self._db.guild(guild_id) as conn:
            if not error and kept is not None:
                cur = await conn.execute(
                    "UPDATE name_answers SET state = 'done', batch = %s, undo_name = %s, note = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (kept.batch, kept.undo_name, kept.note, guild_id, answer.id, answer.claimed_at),
                )
            elif not error:
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

    async def done_for(
        self, guild_id: int, campaign_id: str, entity_id: str, batch: int
    ) -> tuple[str, str | None] | None:
        """The finished split or group answer of this name with this batch: (its action, the list
        as it was), or None."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT action, undo_name FROM name_answers"
                " WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND batch = %s AND state = 'done'"
                " AND action = ANY(%s) ORDER BY created_at DESC LIMIT 1",
                (guild_id, campaign_id, entity_id, batch, list(KEPT_ACTIONS)),
            )
            row = await cur.fetchone()
        return None if row is None else (str(row["action"]), row["undo_name"])

    async def drop_done(self, guild_id: int, campaign_id: str, entity_id: str, batch: int) -> None:
        """The answer that was taken back goes, so it can't be taken back twice."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM name_answers WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND batch = %s AND state = 'done' AND action = ANY(%s)",
                (guild_id, campaign_id, entity_id, batch, list(KEPT_ACTIONS)),
            )

    async def sweep(self, guild_id: int) -> int:
        """Delete answers older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM name_answers WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(
    memory: MemoryStore,
    guild_id: int,
    dm_user_ids: Collection[int],
    answer: Answer,
    kept: Kept | None = None,
    answers: AnswerStore | None = None,
) -> str:
    """Do one answer the way Discord's name check does. Returns "" when it worked, or the
    reason word. Nothing here logs the DM's words. The sender is checked at the moment of use
    (they may have stopped being a DM since); the name must still be a waiting suggestion."""
    if answer.user_id not in dm_user_ids:
        return NOT_DM if answer.action in NEW_ACTIONS else NOT_ALLOWED
    cid = answer.campaign_id
    if answer.action == UNDO:
        return await _undo(memory, answers, guild_id, answer)
    if answer.action in MATCH_ACTIONS:  # (before the waiting-name check: its entity is a known one)
        return await _match(memory, guild_id, answer)
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
        elif answer.action in KEPT_ACTIONS:
            return await _split_or_group(memory, guild_id, cid, entity, answer, kept, answers)
        elif answer.action == "explain":
            await memory.set_note(
                guild_id, cid, entity.id, answer.text, source="dm", limit=NOTE_MAX
            )
        else:
            return FAILED
    except MemoryRuleError:
        return GONE  # the name changed under us (answered in Discord a moment ago)
    return ""


async def _match(memory: MemoryStore, guild_id: int, answer: Answer) -> str:
    """The DM's word on a name DMbot matched on its own, from the page: ✅ confirms it, ↩️ takes it
    back, the same store calls as Discord's check (`confirm_join`, `undo`). Only a match that is
    still waiting can be touched: the batch is looked up in the campaign's waiting matches first,
    so a made-up batch number never reaches `undo` (it would undo any change). Returns "" or the
    page's fixed word (`not_found`: not a waiting match, `answered`: the store refused)."""
    cid = answer.campaign_id
    if not answer.target_id.isdigit():
        return NOT_FOUND_WORD
    batch = int(answer.target_id)
    waiting = await memory.waiting_joins(guild_id, cid)
    if not any(j.batch == batch and j.keep_id == answer.entity_id for j in waiting):
        return NOT_FOUND_WORD
    try:
        if answer.action == CONFIRM_MATCH:
            await memory.confirm_join(guild_id, cid, batch)
        else:
            await memory.undo(guild_id, cid, batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: a name was changed since)
        return NOT_FOUND_WORD if str(exc) in (NOT_FOUND, ALREADY_UNDONE) else ANSWERED
    return ""


def _store_reason(exc: MemoryRuleError) -> str:
    """The store's reason as the page's fixed word."""
    text = str(exc)
    if text == NOT_A_LIST:
        return NOT_A_LIST_WORD
    if text == SPELLING_TAKEN:
        return SPELLING_TAKEN_WORD
    if text == NOT_FOUND:
        return NOT_FOUND_WORD
    return BAD_SPELLING  # anything else the store refuses is about the group's name (or the DM)


async def _split_or_group(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    entity: Any,
    answer: Answer,
    kept: Kept | None,
    answers: AnswerStore | None = None,
) -> str:
    """ "These are separate names", "Yes, that group" and "They're a group" (#3406, #3383): the same
    store calls as Discord's buttons (#3403, #3404), one change each, so one Undo. What stays on
    the row is the batch, the list as it was for a split, and a note of counts and the group's
    name. A refusal changes nothing."""
    note = ""
    undo_name: str | None = None
    made_names: Sequence[str] = ()
    try:
        if answer.action == SPLIT:
            undo_name = entity.name
            split = await memory.split_waiting_name(guild_id, campaign_id, entity.id, source="dm")
            batch = split.batch
            made_names = split.value.made
            note = f"Split into {len(split.value.parts)} names."
        elif answer.action == JOIN_GROUP:
            # Refuse exactly what the hints never offer (and what the door's check lets through
            # only by a hand-made request): a group that is not a confirmed faction, or is hidden
            # from the players. A group with a secret spelling is joined as in Discord (#3404).
            group = await memory.entity(guild_id, campaign_id, answer.target_id)
            if (
                group is None
                or group.status != CONFIRMED
                or group.type != "faction"
                or group.hidden_from_players
            ):
                return NOT_FOUND_WORD
            joined = await memory.join_group(
                guild_id, campaign_id, entity.id, answer.target_id, source="dm"
            )
            batch = joined.batch
            made_names = joined.value.made
            note = f"Added to the group {joined.value.group_name[:150]}."
        else:
            made = await memory.make_group(
                guild_id, campaign_id, entity.id, answer.text, source="dm"
            )
            batch = made.batch
            made_names = made.value.made
            note = f"Made the group {made.value.group_name[:150]}."
    except MemoryRuleError as exc:
        return _store_reason(exc)
    if kept is not None:
        kept.batch, kept.undo_name, kept.note = batch, undo_name, note[:200]
    if answers is not None and made_names:
        # The new waiting names belong to this recap too, or its page would not list them. Not
        # finding them is not a failure: the change is made and Discord's list has them.
        try:
            wanted = {lookup_key(n) for n in made_names}
            waiting = await memory.entities(guild_id, campaign_id, statuses=[PROPOSED])
            await answers.link_like(
                guild_id,
                campaign_id,
                entity.id,
                [e.id for e in waiting if lookup_key(e.name) in wanted],
            )
        except Exception as exc:  # the change is made and can be undone: never lose that
            log.warning("The new names could not be linked to the recap: %s", type(exc).__name__)
    return ""


async def _undo(
    memory: MemoryStore, answers: AnswerStore | None, guild_id: int, answer: Answer
) -> str:
    """Take back a finished split or group of this name by its batch (the answer's target): a
    split with `undo_split` (the waiting claims it set aside come back), a group with the store's
    own undo. Refused as `not_found` when no such finished answer is here (already taken back, or
    swept) and as `answered` when the store refuses (a part was answered, or the names changed)."""
    if answers is None or not answer.target_id.isdigit():
        return NOT_FOUND_WORD
    batch = int(answer.target_id)
    found = await answers.done_for(guild_id, answer.campaign_id, answer.entity_id, batch)
    if found is None:
        return NOT_FOUND_WORD
    action, undo_name = found
    try:
        if action == SPLIT:
            await memory.undo_split(guild_id, answer.campaign_id, batch, undo_name or "")
        else:
            await memory.undo(guild_id, answer.campaign_id, batch)
    except MemoryRuleError:  # (TooLateToUndo too: a part was answered, or the names changed)
        return ANSWERED
    # (the batch number is unique in a campaign, so deleting by it takes only this answer's row)
    await answers.drop_done(guild_id, answer.campaign_id, answer.entity_id, batch)
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
