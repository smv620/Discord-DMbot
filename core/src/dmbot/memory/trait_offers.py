"""A lore upload's lineage, class or language for a character the campaign already has, waiting for
the DM (#3926, Supervisor's ruling of 2026-11-09; languages, #4229 slice 1c).

A trait for an existing character is never written silently: it becomes a question, asked only in
the DM's private chat ("Your lore says Torvin is a dwarf. Keep it?"), because it may spoil a
backstory. ✅ Keep it writes the rules link as the DM's own change (one Undo; a new lineage
replaces the old, a class adds up), and the question goes. ❌ Not this is remembered, so the same
trait is never asked about that character again. "Undo this upload" deletes the questions it left
waiting. Only a confirmed character is asked about; a proposed name keeps its trait on the name.
The rows are this campaign's only (row-level security and composite keys). Counts only in logs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dmbot.consent import still_recorded
from dmbot.db import Database
from dmbot.memory.models import (
    CLASS,
    DM,
    LANGUAGE,
    LINK_NAME_MAX,
    LINK_SOURCE_MAX,
    SPECIES,
    MemoryRuleError,
    Trait,
    name_key,
)

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

QUOTE_MAX = 240  # the reader's quote of the DM's own text, as `memory_link_sources.quote`
UPLOAD_MAX = 200  # the upload's label, as `upload_notices.label`
WAITING, NEVER = "waiting", "never"
# A question whose answer is already true (the DM set that lineage or class since, or the
# character knows that language now, by the DM's word: DMbot's own read of it is the question's,
# #4285), or whose language the DM has said no to on the character (#4362: answered, yes or no), is
# not shown or counted, nor a language question for a
# character that has a sheet since (its languages are the sheet's, #4229): the same rows `waiting`
# and `count` read.
_STILL_OPEN = (
    " AND NOT EXISTS (SELECT 1 FROM memory_rule_links l WHERE l.guild_id = o.guild_id"
    " AND l.campaign_id = o.campaign_id AND l.entity_id = o.entity_id AND l.kind = o.kind"
    " AND lower(l.name) = lower(o.name))"
    " AND NOT (o.kind = 'language' AND (EXISTS (SELECT 1 FROM memory_story_languages s"
    " WHERE s.guild_id = o.guild_id AND s.campaign_id = o.campaign_id"
    " AND s.entity_id = o.entity_id AND s.name_key = o.name_key AND s.given_by IN ('dm', 'not'))"
    " OR EXISTS (SELECT 1 FROM character_sheets h WHERE h.guild_id = o.guild_id"
    " AND h.campaign_id = o.campaign_id AND h.entity_id = o.entity_id)))"
)


class NotWaiting(MemoryRuleError):
    """The question was answered already (another press, or another DM)."""


@dataclass(frozen=True, slots=True)
class Offer:
    """One trait the lore gives an existing character. `current`: the lineage DMbot has for them
    now ("" for none), and `classes` their classes, for the question's words."""

    entity_id: str
    trait: Trait
    quote: str = ""
    upload: str = ""
    entity_name: str = ""
    current: str = ""
    classes: tuple[str, ...] = ()  # the classes DMbot has for them now, for a class question
    # (#4745) a question heard in a session: who said those lines (ids only) and the session, so
    # it goes with their "delete my data" and with the transcript; empty and None for an upload's
    speaker_ids: tuple[int, ...] = ()
    session_id: str | None = None

    @property
    def key(self) -> str:
        return name_key(self.trait.name)


def changes_something(trait: Trait, lineage: str | None, classes: Sequence[str]) -> bool:
    """Whether a trait would change the character: a lineage that is not the one they have (or
    their first), a class they don't have yet. Pure."""
    if trait.kind == SPECIES:
        return lineage is None or name_key(lineage) != name_key(trait.name)
    if trait.kind == CLASS:
        return name_key(trait.name) not in {name_key(c) for c in classes}
    return False


class TraitOfferStore:
    """Writes, reads and answers the waiting questions. The caller has checked who may ask."""

    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    async def offer(
        self,
        guild_id: int,
        campaign_id: str,
        offers: Sequence[Offer],
        notice_id: str | None = None,
    ) -> int:
        """Leave these questions waiting; returns how many are new. One the DM already answered
        "Not this", or that waits already, is left as it is (the first one's speakers and session
        are kept: a later session's hearing adds nothing new to ask), and one whose character is
        not confirmed (or is gone) is left out. One heard in a session (#4745) is written only
        while that session is there and everyone it cites still has lines in it and is still
        recorded, read under the session's row lock, as `MetStore.add` does: the reader's consent
        check was an AI call ago, and a forget in between must not be outrun."""
        added = 0
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            present: dict[str, set[int] | None] = {}
            for o in offers:
                if o.session_id is not None:
                    if o.session_id not in present:
                        present[o.session_id] = await still_there(
                            conn, guild_id, campaign_id, o.session_id
                        )
                    there = present[o.session_id]
                    if there is None or not set(o.speaker_ids) <= there:
                        continue
                t = o.trait
                name, key = t.name[:LINK_NAME_MAX].strip(), o.key[:LINK_NAME_MAX]
                source = t.rules_source[:LINK_SOURCE_MAX] if t.known else ""
                if t.kind not in (SPECIES, CLASS, LANGUAGE) or not name or not key:
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_trait_offers (guild_id, campaign_id, entity_id, kind, name,"
                    " name_key, rules_source, edition, known, quote, upload, notice_id, created_at,"
                    " speaker_ids, session_id)"
                    " SELECT %s, %s, e.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::bigint[], %s"
                    " FROM memory_entities e WHERE e.guild_id = %s AND e.campaign_id = %s"
                    " AND e.id = %s AND e.status = 'confirmed' ON CONFLICT DO NOTHING",
                    (
                        guild_id, campaign_id, t.kind, name, key, source, t.edition,
                        bool(source), " ".join(o.quote.split())[:QUOTE_MAX],
                        o.upload[:UPLOAD_MAX], notice_id, now, sorted(set(o.speaker_ids)),
                        o.session_id, guild_id, campaign_id, o.entity_id,
                    ),
                )  # fmt: skip
                added += cur.rowcount
        return int(added)

    async def waiting(self, guild_id: int, campaign_id: str) -> list[Offer]:
        """The questions waiting, oldest first, with the character's name and current lineage.
        Only live characters' (a merged or rejected one's question is not asked)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT o.entity_id, o.kind, o.name, o.rules_source, o.edition, o.known, o.quote,"
                " o.upload, e.name AS entity_name,"
                " (SELECT l.name FROM memory_rule_links l WHERE l.guild_id = o.guild_id"
                "   AND l.campaign_id = o.campaign_id AND l.entity_id = o.entity_id"
                "   AND l.kind = 'species' LIMIT 1) AS current,"
                " ARRAY(SELECT l.name FROM memory_rule_links l WHERE l.guild_id = o.guild_id"
                "   AND l.campaign_id = o.campaign_id AND l.entity_id = o.entity_id"
                "   AND l.kind = 'class' ORDER BY l.created_at, l.name) AS classes"
                " FROM memory_trait_offers o JOIN memory_entities e ON e.guild_id = o.guild_id"
                " AND e.campaign_id = o.campaign_id AND e.id = o.entity_id"
                " WHERE o.guild_id = %s AND o.campaign_id = %s AND o.state = 'waiting'"
                " AND e.status = 'confirmed'"
                + _STILL_OPEN
                + " ORDER BY o.created_at, o.entity_id, o.kind, o.name_key",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            Offer(
                str(r["entity_id"]),
                Trait(
                    str(r["kind"]),
                    str(r["name"]),
                    str(r["rules_source"]),
                    r["edition"],
                    bool(r["known"]),
                ),
                str(r["quote"]),
                str(r["upload"]),
                str(r["entity_name"]),
                str(r["current"] or ""),
                tuple(str(c) for c in r["classes"] or ()),
            )
            for r in rows
        ]

    async def count(self, guild_id: int, campaign_id: str) -> int:
        """How many wait (what the Questions list counts: the same rows `waiting` shows)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_trait_offers o JOIN memory_entities e"
                " ON e.guild_id = o.guild_id AND e.campaign_id = o.campaign_id"
                " AND e.id = o.entity_id WHERE o.guild_id = %s AND o.campaign_id = %s"
                " AND o.state = 'waiting' AND e.status = 'confirmed'" + _STILL_OPEN,
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def keep(
        self, memory: MemoryStore, guild_id: int, campaign_id: str, offer: Offer
    ) -> int | None:
        """✅ Yes, change it: the rules link, as the DM's own change. The question is claimed first
        in one statement, so two presses (or two DMs) write it once. Returns the change's batch,
        for Undo. Raises `NotWaiting` when it was answered already, and MemoryRuleError when the
        link is refused (the character is no longer one): that question is then remembered as
        "never". Any other failure puts the question back."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_trait_offers WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND kind = %s AND name_key = %s AND state = 'waiting'"
                " RETURNING notice_id",
                (guild_id, campaign_id, offer.entity_id, offer.trait.kind, offer.key),
            )
            claimed = await cur.fetchone()
        if claimed is None:
            raise NotWaiting("That question was already answered.")
        t = offer.trait
        try:
            if t.kind == LANGUAGE:  # (beside the sheets, never a rules link: #4229)
                spoken = await memory.add_story_language(
                    guild_id, campaign_id, offer.entity_id, t.name, source=DM
                )
                return spoken.batch
            written = await memory.set_rule_link(
                guild_id, campaign_id, offer.entity_id, t.kind, t.name, source=DM,
                rules_source=t.rules_source, edition=t.edition, known=t.known,
            )  # fmt: skip
        except MemoryRuleError:
            await self.offer(guild_id, campaign_id, [offer], claimed["notice_id"])
            await self.never(guild_id, campaign_id, offer)
            raise
        except BaseException:
            await self.offer(guild_id, campaign_id, [offer], claimed["notice_id"])
            raise
        return written.batch

    async def never(self, guild_id: int, campaign_id: str, offer: Offer) -> bool:
        """❌ Not this: remembered, so it is never asked again. False when it was not waiting."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE memory_trait_offers SET state = 'never' WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = %s AND kind = %s AND name_key = %s"
                " AND state = 'waiting'",
                (guild_id, campaign_id, offer.entity_id, offer.trait.kind, offer.key),
            )
            return bool(cur.rowcount)

    async def drop_for_notice(self, guild_id: int, campaign_id: str, notice_id: str) -> int:
        """ "Undo this upload": the questions it left waiting go (an answered "Not this" stays)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_trait_offers WHERE guild_id = %s AND campaign_id = %s"
                " AND notice_id = %s AND state = 'waiting'",
                (guild_id, campaign_id, notice_id),
            )
            return int(cur.rowcount)


async def still_there(
    conn: Any, guild_id: int, campaign_id: str, session_id: str
) -> set[int] | None:
    """Who still has lines in this campaign's session and is still recorded, read under the
    session's row lock (the lock "Delete my transcripts" and a speaker's removal take first), or
    None when the session is gone. As `MetStore.add` (#4091, #4153)."""
    cur = await conn.execute(
        "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s AND id = %s"
        " FOR UPDATE",
        (guild_id, campaign_id, session_id),
    )
    if await cur.fetchone() is None:
        return None
    cur = await conn.execute(
        "SELECT DISTINCT user_id FROM transcript_lines WHERE guild_id = %s AND session_id = %s",
        (guild_id, session_id),
    )
    spoke = {int(r["user_id"]) for r in await cur.fetchall()}
    return set(await still_recorded(conn, guild_id, campaign_id, spoke))


async def forget_speaker(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Someone's words are forgotten (Stop recording me, Delete my transcripts, a DM's removal of a
    speaker; #4745): every waiting question heard in their lines goes. A "never" is the DM's own
    answer and stays, without their id (as a kept private scene loses it). `campaign_id`: only
    that campaign's. In the caller's transaction. Returns how many questions went."""
    cur = await conn.execute(
        "DELETE FROM memory_trait_offers WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND state = 'waiting' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE memory_trait_offers SET speaker_ids = array_remove(speaker_ids, %s::bigint)"
        " WHERE guild_id = %s AND %s = ANY(speaker_ids) AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """A session's transcript is deleted: the waiting questions heard in it go too (#4745), and an
    answered one forgets which session it came from. In the caller's transaction. Returns how
    many questions went."""
    cur = await conn.execute(
        "DELETE FROM memory_trait_offers WHERE guild_id = %s AND session_id = %s"
        " AND state = 'waiting'",
        (guild_id, session_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE memory_trait_offers SET session_id = NULL WHERE guild_id = %s AND session_id = %s",
        (guild_id, session_id),
    )
    return gone


async def keep_recorded(
    conn: Any, guild_id: int, campaign_id: str, recorded: Collection[int]
) -> None:
    """A campaign moved to another server (`guild_id` is the new one): a waiting question heard
    from someone not recorded there goes, and an answered one keeps only the ids of those who are,
    as for the private scenes. In the caller's transaction."""
    who = sorted(set(recorded))
    await conn.execute(
        "DELETE FROM memory_trait_offers WHERE guild_id = %s AND campaign_id = %s"
        " AND state = 'waiting' AND NOT (speaker_ids <@ %s::bigint[])",
        (guild_id, campaign_id, who),
    )
    await conn.execute(
        "UPDATE memory_trait_offers SET speaker_ids = ARRAY(SELECT u FROM unnest(speaker_ids)"
        " AS u WHERE u = ANY(%s::bigint[]) ORDER BY u) WHERE guild_id = %s AND campaign_id = %s"
        " AND NOT (speaker_ids <@ %s::bigint[])",
        (who, guild_id, campaign_id, who),
    )
