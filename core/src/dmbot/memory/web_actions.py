"""The bot's side of the website's All names page (#2294): what a DM did there, applied here.

The website has no right on the memory tables, so a DM's action on the page (fix a spelling,
change the kind, add or remove another name, switch a secret, merge, remove) is queued as a row
(`name_actions`). This module claims the rows one at a time in the order they were made and applies
each through the same `MemoryStore` calls as Discord's name card, so the change log, Undo and every
list agree: a change on the page can be undone from the card, and the other way round.

Checked again at the moment of use, because the queue-time check can be stale: the sender must still
be a DM of that campaign, and the name must still be a confirmed one of it. The DM's words (a
spelling, another name) are never logged, only the outcome word; a row is deleted after an hour
whatever happened, and a claimed row that stalled is picked up again.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory import link_edits
from dmbot.memory.link_words import predicate_for_either
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    MERGE_MANY_MAX,
    PLAYER_CHARACTER,
    PRONOUNCED,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    Relation,
    lookup_key,
    name_key,
)
from dmbot.memory.store import PERSON, MemoryStore

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this

# What a failed action says, in words the page can turn into plain ones.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
NOT_FOUND = "not_found"  # the name (or the other name) is gone, or no longer confirmed
BAD_NAME = "bad_name"  # a spelling or another name the rules refuse
BAD_KIND = "bad_kind"  # a kind the rules do not take
EXISTS = "exists"  # the spelling is already another name's
BAD_TARGET = "bad_target"  # a merge, a secret or a removal the rules refuse
FAILED = "failed"  # anything unexpected; the action can be sent again

DECIDE = ("link-confirm", "link-reject")  # a proposed connection, said right or not right
ALIAS_KIND = "nickname"  # what the name card calls an other name the DM adds


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    campaign_id: str
    entity_id: str
    user_id: int
    action: str
    kind: str = ""
    target_id: str = ""
    text: str = ""
    flag: bool = False
    claimed_at: int = 0  # this worker's claim: only it may finish the row


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Action | None:
        """The oldest waiting action (or a stalled one) of this server, marked as being worked
        on. Oldest by `seq`, so one DM's quick "spell" then "merge" run in that order; one worker
        takes them one at a time."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_actions SET state = 'working', claimed_at = %s WHERE (guild_id, id)"
                " = (SELECT guild_id, id FROM name_actions WHERE guild_id = %s AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                # Never past an earlier open action of the campaign: waiting (it may be locked by
                # another worker whose claim has not committed yet) or being applied. The order
                # made is the order done, even with two bots at once.
                " AND NOT EXISTS (SELECT 1 FROM name_actions p WHERE p.guild_id = %s"
                " AND p.campaign_id = name_actions.campaign_id AND p.seq < name_actions.seq"
                " AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, entity_id, user_id, action, kind, target_id, text,"
                " flag",
                (now, guild_id, now - RETAKE_AFTER_S * 1000, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Action(
            row["id"],
            row["campaign_id"],
            row["entity_id"],
            int(row["user_id"]),
            row["action"],
            row["kind"],
            row["target_id"],
            row["text"],
            bool(row["flag"]),
            now,
        )

    async def finish(
        self,
        guild_id: int,
        action: Action,
        *,
        error: str = "",
        result_id: str = "",
        result_other: str = "",
    ) -> bool:
        """Done, or failed with why, for the page to read. Only the worker that holds the current
        claim can write. False: not its row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_actions SET state = %s, error = %s, result_id = %s,"
                " result_other = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error,
                    "" if error else result_id,
                    "" if error else result_other,
                    guild_id,
                    action.id,
                    action.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete actions older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM name_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


@dataclass(frozen=True, slots=True)
class Outcome:
    """`error` is "" when it worked. `result_id` is the name whose row the page shows next: the
    changed name, or the one a merge kept; empty for a removal."""

    error: str = ""
    result_id: str = ""
    result_other: str = ""  # a connection's other name: the pair the page draws next (#2323)


def _failed(why: str) -> Outcome:
    return Outcome(error=why)


async def apply(
    memory: MemoryStore, guild_id: int, dm_user_ids: Collection[int], action: Action
) -> Outcome:
    """Do one action the way Discord's name card does. Nothing here logs the DM's words."""
    if action.user_id not in dm_user_ids:
        return _failed(NOT_ALLOWED)
    cid = action.campaign_id
    entity = await memory.entity(guild_id, cid, action.entity_id)
    if entity is not None and entity.type != PERSON and entity.status != CONFIRMED:
        # A claim taken again after a first run that did its work but did not get to say so:
        # the wanted end state is already there, so it is done, not failed.
        if action.action == "remove" and entity.status == REJECTED:
            return Outcome()
        if action.action == "merge" and entity.merged_into == action.target_id:
            return Outcome(result_id=action.target_id)
    if entity is None or entity.status != CONFIRMED or entity.type == PERSON:
        return _failed(NOT_FOUND)
    try:
        return await _do(memory, guild_id, cid, entity.id, entity.name, action)
    except MemoryRuleError:
        return _failed(NOT_FOUND)  # the name changed under us (another DM, or Discord)


async def _other(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, name: str, text: str
) -> str | None:
    """The id of the entry's other name spelled `text` (not the main name), if it has one."""
    try:
        own, wanted = lookup_key(name), lookup_key(text)
    except MemoryRuleError:
        return None
    if wanted == own:
        return None
    for a in await memory.aliases(guild_id, campaign_id, entity_id=entity_id, include_secret=True):
        if a.key == wanted and a.status != REJECTED:
            return a.id
    return None


async def _do(
    memory: MemoryStore, guild_id: int, cid: str, entity_id: str, name: str, a: Action
) -> Outcome:
    if a.action == "spell":
        new = " ".join(a.text.split())
        if PRONOUNCED.search(new):  # how to say it is not how to spell it (#2332)
            return _failed(BAD_NAME)
        if name_key(new) != name_key(name):
            clash = await memory.confirmed_name_for(guild_id, cid, name_key(new))
            if clash is not None and name_key(clash) != name_key(name):
                return _failed(EXISTS)
        try:
            await memory.rename_entity(guild_id, cid, entity_id, new, source=DM)
        except MemoryRuleError:
            return _failed(BAD_NAME)
        return Outcome(result_id=entity_id)
    if a.action == "kind":
        if a.kind == PLAYER_CHARACTER:
            return _failed(BAD_KIND)  # who plays them is asked in Discord
        try:
            await memory.set_entity_type(guild_id, cid, entity_id, a.kind, source=DM)
        except MemoryRuleError:
            return _failed(BAD_KIND)
        return Outcome(result_id=entity_id)
    if a.action == "add-other":
        text = " ".join(a.text.split())
        clash = await memory.confirmed_name_for(guild_id, cid, name_key(text))
        if clash is not None and name_key(clash) != name_key(name):
            return _failed(EXISTS)
        try:
            if await _other(memory, guild_id, cid, entity_id, name, text) is not None:
                return _failed(EXISTS)  # it already has that name (a secret one, say)
            await memory.add_alias(
                guild_id, cid, entity_id, text, kind=ALIAS_KIND, source=DM, status=CONFIRMED
            )
        except MemoryRuleError:
            return _failed(BAD_NAME)
        return Outcome(result_id=entity_id)
    if a.action == "remove-other":
        alias_id = await _other(memory, guild_id, cid, entity_id, name, a.text)
        if alias_id is None:
            return _failed(NOT_FOUND)
        await memory.update_alias(guild_id, cid, alias_id, source=DM, status=REJECTED)
        return Outcome(result_id=entity_id)
    if a.action == "secret":  # one of its other names
        alias_id = await _other(memory, guild_id, cid, entity_id, name, a.text)
        if alias_id is None:
            return _failed(NOT_FOUND)
        try:
            await memory.update_alias(guild_id, cid, alias_id, source=DM, secret=a.flag)
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "hide":  # "Players don't know this one yet", the whole entry
        try:
            await memory.set_hidden_from_players(guild_id, cid, entity_id, a.flag, source=DM)
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "merge":
        keep = await memory.entity(guild_id, cid, a.target_id)
        if keep is None or keep.status != CONFIRMED or keep.type == PERSON or keep.id == entity_id:
            return _failed(BAD_TARGET)
        try:
            await memory.merge(guild_id, cid, keep.id, entity_id, source=DM, dm_said_same=True)
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=keep.id)
    if a.action == "merge-many":  # the kept name is the row's entry; `text` holds the others
        ids = list(dict.fromkeys(a.text.split()))
        if not ids or entity_id in ids or len(ids) > MERGE_MANY_MAX:
            return _failed(BAD_TARGET)
        waiting: list[str] = []
        for other_id in ids:
            other = await memory.entity(guild_id, cid, other_id)
            if other is not None and other.merged_into == entity_id:
                continue  # a retried job: this one went in the first time
            if other is None or other.status != CONFIRMED or other.type == PERSON:
                return _failed(NOT_FOUND)
            waiting.append(other_id)
        if waiting:
            try:
                await memory.merge_many(guild_id, cid, entity_id, waiting, source=DM)
            except MemoryRuleError:
                return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "remove":
        if not a.flag:
            return _failed(BAD_TARGET)  # (the door asks for the confirmation; so does this)
        await memory.set_entity_status(guild_id, cid, entity_id, REJECTED, source=DM)
        return Outcome()
    if a.action.startswith("link-"):
        return await _link(memory, guild_id, cid, entity_id, a)
    return _failed(FAILED)


async def _link(memory: MemoryStore, guild_id: int, cid: str, entity_id: str, a: Action) -> Outcome:
    """The relationship web's edits (#2323), through the calls the name card makes. The outcome
    names the pair, so the page can draw the edge as it now stands."""
    if a.action == "link-add":
        other = await memory.entity(guild_id, cid, a.target_id)
        if (
            other is None
            or other.status != CONFIRMED
            or other.type == PERSON
            or other.id == entity_id
        ):
            return _failed(BAD_TARGET)
        predicate, detail, swapped = predicate_for_either(a.text)
        # "has a niece" typed on Aldric's card is stored as "Mira kin_of Aldric, niece" (#2457)
        first, second = (other.id, entity_id) if swapped else (entity_id, other.id)
        try:
            await memory.add_relation(
                guild_id, cid, first, predicate, second, source=DM, confidence=1.0,
                status=CONFIRMED, detail=detail[:100], secret=a.flag,
                directional=predicate == "kin_of" and bool(detail),
            )  # fmt: skip
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id, result_other=other.id)
    # the others name one connection, filed under its first name
    link = await memory.relation(guild_id, cid, a.target_id)
    if link is None or entity_id not in (link.subject_id, link.object_id):
        return _failed(NOT_FOUND)
    pair = {"result_id": link.subject_id, "result_other": link.object_id}
    if a.action in DECIDE:
        return await _decide(memory, guild_id, cid, link, a, pair)
    if link.status != CONFIRMED:
        # A retried job (the first try did its work, then stopped before it was marked done) finds
        # the connection already ended: that is "done", as for a remove or merge in `apply`.
        if link.status == REJECTED and await _already(memory, guild_id, cid, link, a):
            return Outcome(**pair)
        return _failed(NOT_FOUND)
    try:
        if a.action == "link-secret":
            await link_edits.set_secret(memory, guild_id, cid, link, a.flag)
        elif a.action == "link-pin":
            # 📌 100% truth (#2447): the card's own call. A retried job finds it already so.
            if (link.id in await memory.pinned_connections(guild_id, cid)) != a.flag:
                await memory.pin(guild_id, cid, link.id, a.flag, source=DM)
        elif a.action == "link-remove":
            if not a.flag:
                return _failed(BAD_TARGET)
            await link_edits.remove(memory, guild_id, cid, link)
        elif a.action == "link-words":
            predicate, detail, swapped = predicate_for_either(a.text)
            other_end = link.object_id if entity_id == link.subject_id else link.subject_id
            await link_edits.change_words(
                memory,
                guild_id,
                cid,
                link,
                predicate,
                detail,
                first=other_end if swapped else entity_id,
            )  # (a family role is said from the name it belongs to, #2457)
        else:
            return _failed(FAILED)
    except MemoryRuleError:
        return _failed(BAD_TARGET)
    return Outcome(**pair)


async def _decide(
    memory: MemoryStore, guild_id: int, cid: str, link: Relation, a: Action, pair: dict[str, str]
) -> Outcome:
    """The DM's Confirm or Not right on a connection PlotBot proposed (#2635), through the calls
    the connection card's edits use. A retried job finds it already decided the same way, which
    is "done"; decided the other way, or ended, is not found."""
    want = CONFIRMED if a.action == "link-confirm" else REJECTED
    if link.status == want:
        return Outcome(**pair)
    if link.status != PROPOSED:
        return _failed(NOT_FOUND)
    try:
        if want == CONFIRMED:
            await link_edits.confirm(memory, guild_id, cid, link)
        else:
            await link_edits.remove(
                memory, guild_id, cid, link
            )  # (rejected, and Undo puts it back)
    except MemoryRuleError:
        return _failed(BAD_TARGET)
    return Outcome(**pair)


async def _already(memory: MemoryStore, guild_id: int, cid: str, link: Relation, a: Action) -> bool:
    """Whether an ended connection is the work of this very action: a removal (it is ended), or
    new words (a live connection with those words stands between the same two names)."""
    if a.action == "link-remove":
        return a.flag
    if a.action != "link-words":
        return False
    predicate, detail, _ = predicate_for_either(a.text)
    return any(
        r.status == CONFIRMED
        and r.predicate == predicate
        and r.detail.casefold() == detail.casefold()
        and {r.subject_id, r.object_id} == {link.subject_id, link.object_id}
        for r in await memory.relations(
            guild_id, cid, entity_id=link.subject_id, include_secret=True
        )
    )
