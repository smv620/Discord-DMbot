"""A lore upload's lineage or class for a character the campaign already has, waiting for the DM
(#3926, Supervisor's ruling of 2026-11-09).

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.db import Database
from dmbot.memory.models import (
    CLASS,
    DM,
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
# A question whose answer is already true (the DM set that lineage or class since) is not shown or
# counted: the same rows `waiting` and `count` read.
_STILL_OPEN = (
    " AND NOT EXISTS (SELECT 1 FROM memory_rule_links l WHERE l.guild_id = o.guild_id"
    " AND l.campaign_id = o.campaign_id AND l.entity_id = o.entity_id AND l.kind = o.kind"
    " AND lower(l.name) = lower(o.name))"
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
        "Not this", or that waits already, is left as it is, and one whose character is not
        confirmed (or is gone) is left out."""
        added = 0
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            for o in offers:
                t = o.trait
                name, key = t.name[:LINK_NAME_MAX].strip(), o.key[:LINK_NAME_MAX]
                source = t.rules_source[:LINK_SOURCE_MAX] if t.known else ""
                if t.kind not in (SPECIES, CLASS) or not name or not key:
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_trait_offers (guild_id, campaign_id, entity_id, kind, name,"
                    " name_key, rules_source, edition, known, quote, upload, notice_id, created_at)"
                    " SELECT %s, %s, e.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                    " FROM memory_entities e WHERE e.guild_id = %s AND e.campaign_id = %s"
                    " AND e.id = %s AND e.status = 'confirmed' ON CONFLICT DO NOTHING",
                    (
                        guild_id, campaign_id, t.kind, name, key, source, t.edition,
                        bool(source), " ".join(o.quote.split())[:QUOTE_MAX],
                        o.upload[:UPLOAD_MAX], notice_id, now, guild_id, campaign_id, o.entity_id,
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
