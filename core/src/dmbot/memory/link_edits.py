"""Changing one connection that is already there: its words, who sees it, taking it back (#2323).

The name card and the website's relationship web edit connections the same way, so both call
these. Each is logged in the campaign's change log, so the history sees the same thing whichever
side the DM used. A removal is one change and Undo can put it back; new words are one change
too (the new connection and the old one ended together), so one Undo reverses either. The DM's words
are never logged here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence

from dmbot.memory import kin
from dmbot.memory.models import DM, REJECTED, MemoryRuleError, Relation, Written
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


async def set_secret(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation, secret: bool
) -> Written[Relation]:
    """Hide the connection from players, or show it to them. Showing one the story was holding
    back (a backstory connection, #2404) also reveals it, in the same change: the DM's one
    "players can see it" switch, refused until both its names are shown."""
    reveal = False if link.unrevealed and not secret else None
    return await memory.update_relation(
        guild_id,
        campaign_id,
        link.id,
        source=DM,
        secret=secret,
        unrevealed=reveal,
        dm_chose_secret=True,
    )


ONLY_THE_DM, SOME_CHARACTERS, EVERYONE = "dm", "some", "everyone"
WHO_KNOWS = (ONLY_THE_DM, SOME_CHARACTERS, EVERYONE)
COMMON = "common"  # 🌍 Common knowledge (#4537 slice 1b): every character, NPCs and later joiners
PICK_WHO = (
    "Pick who knows it: only the DM, some characters, everyone at the table, or common knowledge."
)
PICK_ONE = "Pick at least one character."


def who_knows_now(link: Relation, knowers: Sequence[str]) -> str:
    """Which of the three answers a connection is at now: shown to everyone, known by these
    characters, or only the DM's (#2935)."""
    if not (link.secret or link.unrevealed):
        return EVERYONE
    return SOME_CHARACTERS if knowers else ONLY_THE_DM


async def set_who_knows(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    who: str,
    characters: Sequence[str] = (),
    *,
    players_only: bool = False,
    made: list[int] | None = None,
    listed: Collection[str] | None = None,
) -> Written[object]:
    """ "Who knows this?" for one connection, with its fourth answer, 🌍 Common knowledge (#4537
    slice 1b). See `with_common`; the flags are `_set_flags`. `listed`: the characters the DM's
    list showed (the website's, #4968): DMbot's guess of one of them kept is the DM's word, and
    one the list never showed is left as it is."""
    ends = (link.subject_id, link.object_id)
    shown = await memory.entries_shown(guild_id, campaign_id, ends)
    hidden_id = next((e for e in ends if e not in shown), None)
    hidden_end = (
        await memory.entity(guild_id, campaign_id, hidden_id) if hidden_id is not None else None
    )
    hidden_name = hidden_end.name if hidden_end is not None else "both names"

    async def flags(answer: str, chosen: Sequence[str], log: list[int]) -> Written[object]:
        return await _set_flags(
            memory, guild_id, campaign_id, link, answer, chosen,
            players_only=players_only, made=log, listed=listed,
        )  # fmt: skip

    return await with_common(
        memory, guild_id, campaign_id, "connection", link.id,
        shown=not (link.secret or link.unrevealed),
        cannot_show=(
            f"Show {hidden_name} to the players first: common knowledge is something they see."
            if hidden_id is not None
            else ""
        ),
        who=who, characters=characters, set_flags=flags, made=made,
    )  # fmt: skip


FlagSetter = Callable[[str, Sequence[str], list[int]], Awaitable[Written[object]]]


async def with_common(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    kind: str,
    item_id: str,
    *,
    shown: bool,
    cannot_show: str,
    who: str,
    characters: Sequence[str],
    set_flags: FlagSetter,
    made: list[int] | None,
) -> Written[object]:
    """The four answers of "Who knows this?" for a connection or an other name (#4537 slice 1b).

    🌍 Common knowledge: every character knows it, NPCs too, and anyone who joins later. It shows
    the item to the players first if it is hidden (common knowledge is only counted while shown,
    the gate's rule), then writes the common row; refused with `cannot_show` when the item can't be
    shown (an end or its entry kept from the players), since the page would claim what nobody sees.
    Any other answer on a common item takes common knowledge back first.

    Every pick is checked before anything is written, so a refused pick writes nothing. All of it
    is the DM's own choice, each change's batch in `made`, so one ↩ Undo takes back the whole
    answer (#4159). If a later step fails, the earlier ones are taken back before the error goes
    on: the item is never left half changed (shown with no common row, or common knowledge gone
    with the flags unchanged). Two DMs pressing at once can still interleave the two or three
    writes, as the other two-write answers can."""
    log = made if made is not None else []
    if who not in (*WHO_KNOWS, COMMON):
        raise MemoryRuleError(PICK_WHO)
    if who == SOME_CHARACTERS and not characters:
        raise MemoryRuleError(PICK_ONE)
    await memory.check_knowers(
        guild_id, campaign_id, list(characters) if who == SOME_CHARACTERS else []
    )
    common = item_id in await memory.common_knowledge(guild_id, campaign_id, kind, [item_id])
    if who == COMMON and common and shown:
        return Written(None, None)  # already common knowledge
    if who == COMMON and cannot_show:
        raise MemoryRuleError(cannot_show)
    if who != COMMON and not common:
        return await set_flags(who, characters, log)
    first = len(log)
    try:
        if who == COMMON:
            result = await set_flags(EVERYONE, (), log)
            if not common:
                result = keep_batch(
                    log,
                    await memory.add_common_knowledge(
                        guild_id, campaign_id, kind, [item_id], source=DM
                    ),
                )
            return result
        removed = keep_batch(
            log, await memory.remove_common_knowledge(guild_id, campaign_id, kind, [item_id])
        )
        result = await set_flags(who, characters, log)
        return result if result.batch is not None else removed
    except Exception:
        await _take_back(memory, guild_id, campaign_id, log[first:])
        del log[first:]
        raise


async def _take_back(
    memory: MemoryStore, guild_id: int, campaign_id: str, batches: Sequence[int]
) -> None:
    """Undo these writes, newest first, after a later step failed. A write that can't be taken
    back is logged by its type and skipped, so the first error is the one that goes on."""
    for batch in reversed(batches):
        try:
            await memory.undo(guild_id, campaign_id, batch, source=DM)
        except Exception as exc:  # (never hide the error that started this)
            log.warning("A who-knows change could not be taken back: %s", type(exc).__name__)


async def _set_flags(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    who: str,
    characters: Sequence[str] = (),
    *,
    players_only: bool = False,
    made: list[int] | None = None,
    listed: Collection[str] | None = None,
) -> Written[object]:
    """ "Who knows this?" for one connection (#2935), the one call the name card and the website
    share. Only the DM (hidden from the party, no knowers), only these player characters (hidden
    from the party, each of those characters' own point of view holds it), or everyone at the table
    (their knowers let go, then shown to the players: the same as the old "show to players").
    The three keep the meaning of the old 🤫 and 👁️ switches. Returns the last change that wrote
    something. One answer can be two changes (hide it, then say who knows it): `made`, when given,
    gets every change's batch in order, so an Undo can take back all of them (#4159)."""
    log = made if made is not None else []
    if who not in WHO_KNOWS:
        raise MemoryRuleError("Pick who knows it: only you, some characters, or everyone.")
    if who == SOME_CHARACTERS and not characters:
        raise MemoryRuleError(PICK_ONE)
    if who == EVERYONE:
        if not (link.secret or link.unrevealed):
            return Written(None, None)  # already everyone's: nothing to write
        before = (await memory.connection_knowers(guild_id, campaign_id, [link.id])).get(
            link.id, []
        )
        if before:
            keep_batch(
                log,
                await memory.set_connection_knowers(
                    guild_id,
                    campaign_id,
                    link.id,
                    [],
                    source=DM,
                    players_only=players_only,
                    dm_chose_secret=True,
                ),
            )
        try:
            return keep_batch(log, await set_secret(memory, guild_id, campaign_id, link, False))
        except MemoryRuleError:  # (refused until its names are shown): put who knew it back
            if before:
                await memory.set_connection_knowers(
                    guild_id,
                    campaign_id,
                    link.id,
                    before,
                    source=DM,
                    players_only=players_only,
                    dm_chose_secret=True,
                )
            raise
    chosen = list(characters) if who == SOME_CHARACTERS else []
    await memory.check_knowers(guild_id, campaign_id, chosen)  # nothing is hidden on a bad pick
    hidden: Written[object] = Written(None, None)
    if not (link.secret or link.unrevealed):
        hidden = keep_batch(log, await set_secret(memory, guild_id, campaign_id, link, True))
    knew = keep_batch(
        log,
        await memory.set_connection_knowers(
            guild_id,
            campaign_id,
            link.id,
            chosen,
            source=DM,
            players_only=players_only,
            dm_chose_secret=True,
            listed=listed,
        ),
    )
    # (hiding it with no one to know it is the only change: that one is what Undo takes back, #4159)
    return knew if knew.batch is not None else hidden


def keep_batch[T](log: list[int], written: Written[T]) -> Written[T]:
    """Note a change's batch (none when it wrote nothing) and pass the change on."""
    if written.batch is not None:
        log.append(written.batch)
    return written


async def remove(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation
) -> Written[Relation]:
    """Take the connection back. History is kept, and Undo can put it back (#1566)."""
    return await memory.update_relation(guild_id, campaign_id, link.id, source=DM, status=REJECTED)


async def change_words(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    predicate: str,
    detail: str,
    first: str | None = None,
    *,
    dm_feeling: bool = False,
) -> Written[Relation] | None:
    """New words between the same two names, keeping who sees it, in ONE change (so one Undo
    puts the old words back). None when the words are the ones it already has, capitals aside:
    nothing is written (`dm_feeling`: unless the DM's Reputation changed, #3642). The store does
    the work, for the website's edits too.

    A family role the DM types ("is the niece of") gives the connection a direction (#2457), even
    over an older connection that already says "niece" (its direction was arbitrary): `first` is
    the name the words are said from, who holds the role, and the connection is written in that
    order."""
    detail = detail[:100]
    directional = predicate == "kin_of" and kin.directional(detail)
    # Only a one-way role ("niece") has a side to be on; "cousin" reads the same both ways, so
    # retyping it must write nothing.
    swap = directional and first is not None and first == link.object_id
    if (
        (predicate, detail.casefold()) == (link.predicate, link.detail.casefold())
        and not (directional and (not link.directional or swap))
        and not dm_feeling  # (the same word picked again still sets the DM's Reputation, #3642)
    ):
        return None
    written = await memory.change_relation_words(
        guild_id, campaign_id, link.id, predicate, detail, source=DM,
        directional=directional, swap=swap, dm_feeling=dm_feeling,
    )  # fmt: skip
    unchanged = written.value.id == link.id and written.value.directional == link.directional
    # (with `dm_feeling` the same words may still have set the DM's Reputation: an empty batch
    # means nothing was written)
    if dm_feeling:
        return written if written.batch is not None else None
    return None if unchanged and not swap else written
