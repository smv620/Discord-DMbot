"""Which upload a name was read from (#3675 Part B): one row per entry and upload, a re-read is the
same row, a merged-away entry's uploads read as the kept one's, and the lore read writes it only
for the names it made. Invented names and labels."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.ui import name_background
from dmbot.ui.name_lists import Upload
from tests.pg import DatabaseTest

GUILD, DM = 1, 7
LORE = "Tavin's backstory, 24 Oct"


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.tran = await self.person("Tran Vosk")

    async def person(self, name: str, campaign: str | None = None) -> str:
        written = await self.memory.add_entity(
            GUILD, campaign or self.campaign.id, type="character", name=name, source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        return written.value.id

    async def test_a_read_is_recorded_once_and_read_back(self) -> None:
        cid = self.campaign.id
        added = await self.memory.record_name_sources(
            GUILD, cid, [self.tran], upload_key="up1", label=LORE
        )
        again = await self.memory.record_name_sources(
            GUILD, cid, [self.tran], upload_key="up1", label=LORE
        )
        self.assertEqual((added, again), (1, 0))
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tran), [("up1", LORE)])

    async def test_no_key_uses_the_label_and_a_gone_entry_is_left_out(self) -> None:
        cid = self.campaign.id
        n = await self.memory.record_name_sources(
            GUILD, cid, [self.tran, "f" * 32], upload_key="", label=LORE
        )
        self.assertEqual(n, 1)
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tran), [(LORE, LORE)])

    async def test_a_merged_away_entry_s_uploads_read_as_the_kept_one_s(self) -> None:
        cid = self.campaign.id
        other = await self.person("Tran the Elder")
        await self.memory.record_name_sources(GUILD, cid, [other], upload_key="up2", label="Map")
        await self.memory.merge(GUILD, cid, self.tran, other, source=DM_SOURCE, dm_said_same=True)
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tran), [("up2", "Map")])

    async def test_undoing_the_merge_gives_the_uploads_back(self) -> None:
        cid = self.campaign.id
        other = await self.person("Tran the Elder")
        await self.memory.record_name_sources(GUILD, cid, [other], upload_key="up2", label="Map")
        merged = await self.memory.merge(
            GUILD, cid, self.tran, other, source=DM_SOURCE, dm_said_same=True
        )
        assert merged.batch is not None
        await self.memory.undo(GUILD, cid, merged.batch)
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tran), [])
        self.assertEqual(await self.memory.name_sources(GUILD, cid, other), [("up2", "Map")])

    async def test_a_chain_of_merges_reads_through(self) -> None:
        cid = self.campaign.id
        a, b = await self.person("Old Tran"), await self.person("Tran the Elder")
        await self.memory.record_name_sources(GUILD, cid, [a], upload_key="up5", label="Notes")
        await self.memory.merge(GUILD, cid, b, a, source=DM_SOURCE, dm_said_same=True)
        await self.memory.merge(GUILD, cid, self.tran, b, source=DM_SOURCE, dm_said_same=True)
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tran), [("up5", "Notes")])

    async def test_a_list_split_into_names_keeps_its_upload_on_each(self) -> None:
        cid = self.campaign.id
        written = await self.memory.add_entity(
            GUILD, cid, type="place", name="Aldmoor, Quillgate, Fenwick", source=DM_SOURCE,
            status="proposed",
        )  # fmt: skip
        listed = written.value.id
        await self.memory.record_name_sources(GUILD, cid, [listed], upload_key="up6", label=LORE)
        split = await self.memory.split_waiting_name(GUILD, cid, listed, source=DM_SOURCE)
        data = await self.memory.lookup_data(GUILD, cid)
        parts = [e.id for e in data.entities if e.name in split.value.made]
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertEqual(await self.memory.name_sources(GUILD, cid, part), [("up6", LORE)])

    async def test_another_campaign_s_rows_are_never_read(self) -> None:
        second = await self.campaigns.create(GUILD, "Another", DM)
        theirs = await self.person("Tran Vosk", second.id)
        await self.memory.record_name_sources(
            GUILD, second.id, [theirs], upload_key="up3", label=LORE
        )
        # (the same id asked for in this campaign finds nothing, and this campaign can't write it)
        self.assertEqual(await self.memory.name_sources(GUILD, self.campaign.id, theirs), [])
        self.assertEqual(
            await self.memory.record_name_sources(
                GUILD, self.campaign.id, [theirs], upload_key="up4", label=LORE
            ),
            0,
        )


class TheUnnamed(unittest.IsolatedAsyncioTestCase):
    """`name_background._unnamed_rows` hands back the unnamed people it made (not one it found),
    so the read records their upload too."""

    async def test_only_a_person_made_now_is_collected(self) -> None:
        from dmbot.memory.models import Entity

        def entry(eid: str) -> Entity:
            return Entity(eid, "character", "x", "", "proposed", None, "background", 0)

        made_one = SimpleNamespace(entity=entry("u" * 32), created=True)
        found_one = SimpleNamespace(entity=entry("v" * 32), created=False)
        memory: Any = SimpleNamespace(add_unnamed=AsyncMock(side_effect=[made_one, found_one]))
        link: Any = SimpleNamespace(
            fixed="Tran Vosk", what="a hooded figure", anchored=False, role="", how="knows",
            fixed_is_a=True, quote="", secret=False, words="", decided=None,
        )  # fmt: skip
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        made: list[str] = []
        await name_background._unnamed_rows(
            memory, campaign, [link, link], lambda _n: "t" * 32, False, LORE, None, made=made
        )
        self.assertEqual(made, ["u" * 32])


class TheLoreRead(unittest.IsolatedAsyncioTestCase):
    """`name_background._record_sources`, the write after the lore or backstory read stores its
    names, on a stand-in store."""

    async def run_it(self, memory: Any, upload: Upload) -> None:
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        stored = {"character": ["a" * 32], "place": ["b" * 32]}
        await name_background._record_sources(memory, campaign, stored, upload, LORE)

    async def test_the_website_s_upload_id_is_the_key(self) -> None:
        memory = SimpleNamespace(record_name_sources=AsyncMock(return_value=2))
        await self.run_it(memory, Upload("text", "Tavin's backstory", True, source_id="up9"))
        memory.record_name_sources.assert_awaited_once_with(
            GUILD, "c", ["a" * 32, "b" * 32], upload_key="up9", label=LORE
        )

    async def test_a_discord_upload_is_keyed_by_its_dated_label(self) -> None:
        memory = SimpleNamespace(record_name_sources=AsyncMock(return_value=2))
        await self.run_it(memory, Upload("text", "Tavin's backstory", True))
        self.assertEqual(memory.record_name_sources.await_args.kwargs["upload_key"], LORE)

    async def test_nothing_made_writes_nothing(self) -> None:
        memory: Any = SimpleNamespace(record_name_sources=AsyncMock())
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        await name_background._record_sources(memory, campaign, {}, Upload("t", "x"), LORE)
        memory.record_name_sources.assert_not_awaited()

    async def test_a_failed_write_keeps_the_names_and_logs_the_type_only(self) -> None:
        memory = SimpleNamespace(
            record_name_sources=AsyncMock(side_effect=RuntimeError("row says Tran Vosk"))
        )
        with self.assertLogs("dmbot.ui.name_background", "WARNING") as logs:
            await self.run_it(memory, Upload("text", "Tavin's backstory", True))
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("Tran", logs.output[0])


if __name__ == "__main__":
    unittest.main()
