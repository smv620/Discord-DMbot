"""Unnamed names stay unique on every path that changes one (#3743): the DM making an entry
unnamed, an anchor rejected, a merge re-anchoring, an anchor renamed, and the retag that tells
apart names Python's `name_key` takes as one. Each case is a collision that gets the
"(first seen …)" a new person would. Real Postgres; invented names."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory.models import CONFIRMED, DM
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

GUILD, OWNER = 7701, 1
SEVEN_NOV = int(datetime(2026, 11, 7, 19, 5, tzinfo=UTC).timestamp())
TAG = " (first seen 7 Nov 2026)"


class Paths(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db, clock=lambda: SEVEN_NOV)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", OWNER)).id

    async def entry(self, name: str, **kw: Any) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, type="npc", name=name, source=DM, status=CONFIRMED, **kw
        )
        return str(made.value.id)

    async def person(self, what: str, anchor: str | None = None) -> str:
        made = await self.memory.add_unnamed(GUILD, self.c, what=what, anchor_id=anchor, read={})
        return str(made.entity.id)

    async def name_of(self, eid: str) -> str:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT name FROM memory_entities WHERE id = %s", (eid,))
            row = await cur.fetchone()
        assert row is not None
        return str(row["name"])

    async def test_making_an_entry_unnamed_beside_one_that_reads_the_same(self) -> None:
        await self.person("the woman")
        made = await self.memory.add_entity(
            GUILD, self.c, type="character", name="the woman", source="extractor"
        )
        got = await self.memory.make_unnamed(GUILD, self.c, str(made.value.id), source=DM)
        self.assertEqual(got.value.name, "unnamed - the woman" + TAG)

    async def test_an_anchor_rejected_with_two_sisters_of_two_kens(self) -> None:
        ken = await self.entry("Ken")
        first = await self.person("sister")  # (already "unnamed - sister", with no anchor)
        mine = await self.person("sister", ken)
        rook = await self.entry("Rook")
        await self.memory.add_relation(
            GUILD, self.c, mine, "knows", rook, source=DM, confidence=1.0, status=CONFIRMED
        )  # (another tie, so she is kept when Ken goes)
        await self.memory.set_entity_status(GUILD, self.c, ken, "rejected", source=DM)
        self.assertEqual(await self.name_of(first), "unnamed - sister")
        self.assertEqual(await self.name_of(mine), "unnamed - sister" + TAG)

    async def test_a_merge_puts_two_sisters_on_one_ken(self) -> None:
        ken, twin = await self.entry("Ken"), await self.entry("Kenneth")
        await self.alias(twin, "Ken")
        one, two = await self.person("sister", ken), await self.person("sister", twin)
        self.assertNotEqual(await self.name_of(one), await self.name_of(two))
        await self.memory.merge(GUILD, self.c, ken, twin, source=DM, dm_said_same=True)
        names = {await self.name_of(one), await self.name_of(two)}
        self.assertEqual(names, {"unnamed - Ken's sister", "unnamed - Ken's sister" + TAG})

    async def alias(self, entity: str, text: str) -> None:
        await self.memory.add_alias(
            GUILD, self.c, entity, text, kind="short", source=DM, status=CONFIRMED
        )

    async def test_a_renamed_anchor_that_now_reads_like_another(self) -> None:
        ken, bran = await self.entry("Ken"), await self.entry("Bran")
        await self.person("sister", ken)
        theirs = await self.person("sister", bran)
        await self.memory.rename_entity(GUILD, self.c, bran, "Ken", source=DM)
        self.assertEqual(await self.name_of(theirs), "unnamed - Ken's sister" + TAG)

    async def test_the_retag_tells_apart_what_name_key_takes_as_one(self) -> None:
        old = await self.person("hooded figure")
        accented = await self.person("hooded fígure")  # (an accent: one name to `name_key`)
        async with self.db.guild(GUILD) as conn:  # (as migration 0379 left them, both untagged)
            await conn.execute(
                "UPDATE memory_entities SET name = 'unnamed - hooded fígure',"
                " unnamed_what = 'hooded fígure', created_at = created_at + 1 WHERE id = %s",
                (accented,),
            )
        self.assertEqual(await self.memory.retag_unnamed(GUILD, self.c, dry_run=True), 1)
        self.assertEqual(await self.name_of(accented), "unnamed - hooded fígure")  # (nothing yet)
        self.assertEqual(await self.memory.retag_unnamed(GUILD, self.c), 1)
        self.assertEqual(await self.name_of(old), "unnamed - hooded figure")
        self.assertEqual(await self.name_of(accented), "unnamed - hooded fígure" + TAG)
        self.assertEqual(await self.memory.retag_unnamed(GUILD, self.c), 0)  # (a rerun: nothing)
