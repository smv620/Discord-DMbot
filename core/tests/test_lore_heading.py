"""The lore section a name was read under (#5050): `lore_knowledge.first_headings` finds the heading
above each name's first whole-word place in an upload, and the store keeps it beside the upload's
label, copies it to a split list's parts and reads the earliest back for the character card.
Invented names and documents."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.memory import lore_knowledge
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.ui import name_background
from dmbot.ui.name_lists import Upload
from tests.pg import DatabaseTest

GUILD, DM = 1, 7
LORE = "Hollowmere notes, 22 Nov"

TEXT = """Hollowmere notes

The town of Brindle sits on the lake. Nobody trusts the ferryman.

# Brindle

Tamsin Reedwell keeps the Thornwatch inn. Her cousin Wenna runs the mill.

Old Roads

The road north passes the shrine. Captain Varn guards it.
Wenna once lived here too.
"""


class FirstHeadings(unittest.TestCase):
    def test_each_name_gets_the_heading_above_its_first_place(self) -> None:
        got = lore_knowledge.first_headings(
            TEXT,
            {
                "tamsin": ("Tamsin Reedwell",),
                "varn": ("Captain Varn",),
                "wenna": ("Wenna",),  # (first under Brindle, later under Old Roads)
            },
        )
        self.assertEqual(got, {"tamsin": "Brindle", "varn": "Old Roads", "wenna": "Brindle"})

    def test_the_name_itself_decides_and_other_names_only_when_it_is_missing(self) -> None:
        got = lore_knowledge.first_headings(TEXT, {"varn": ("Captain Varn", "the ferryman")})
        self.assertEqual(got, {"varn": "Old Roads"})  # (not an earlier "the ferryman")
        got = lore_knowledge.first_headings(TEXT, {"varn": ("Captain Varn Holt", "the ferryman")})
        self.assertEqual(got, {"varn": "Hollowmere notes"})

    def test_any_line_break_ends_a_heading_line(self) -> None:
        text = "Intro\n\nSome words here.\x0cTamsin Reedwell\n\nTamsin keeps the inn.\n"
        self.assertEqual(
            lore_knowledge.first_headings(text, {"t": ("Tamsin Reedwell",)}), {"t": "Intro"}
        )
        crlf = "Brindle\r\n\r\nTamsin keeps the inn.\r\n"
        self.assertEqual(lore_knowledge.first_headings(crlf, {"t": ("Tamsin",)}), {"t": "Brindle"})

    def test_whole_words_any_case_and_line_breaks_inside_a_name(self) -> None:
        text = "Notes\n\nThe innkeeper is TAMSIN\nReedwell. Tamsinia is someone else.\n"
        self.assertEqual(
            lore_knowledge.first_headings(text, {"t": ("Tamsin Reedwell",), "x": ("Tams",)}),
            {"t": "Notes"},
        )

    def test_nothing_above_or_not_found_gives_no_key(self) -> None:
        text = "Tamsin keeps the inn and nobody wrote a heading.\n"
        self.assertEqual(
            lore_knowledge.first_headings(text, {"t": ("Tamsin",), "n": ("Nobody Here",)}), {}
        )
        self.assertEqual(lore_knowledge.first_headings("", {"t": ("Tamsin",)}), {})

    def test_a_name_in_a_heading_is_under_the_section_above(self) -> None:
        self.assertEqual(
            lore_knowledge.first_headings(TEXT, {"b": ("Brindle",)}), {"b": "Hollowmere notes"}
        )
        text = "# Brindle\n\nA town on the lake.\n"
        self.assertEqual(lore_knowledge.first_headings(text, {"b": ("Brindle",)}), {})


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Hollowmere", DM)
        self.tamsin = await self.person("Tamsin Reedwell")

    async def person(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        return written.value.id

    async def test_the_heading_is_kept_and_the_earliest_upload_is_read_back(self) -> None:
        cid = self.campaign.id
        self.assertIsNone(await self.memory.lore_origin(GUILD, cid, self.tamsin))
        await self.memory.record_name_sources(
            GUILD, cid, [self.tamsin], upload_key="up1", label=LORE,
            headings={self.tamsin: "Brindle"},
        )  # fmt: skip
        await self.memory.record_name_sources(
            GUILD, cid, [self.tamsin], upload_key="up2", label="Later notes",
            headings={self.tamsin: "Elsewhere"},
        )  # fmt: skip
        self.assertEqual(await self.memory.lore_origin(GUILD, cid, self.tamsin), (LORE, "Brindle"))

    async def test_no_heading_reads_empty_and_the_old_call_still_works(self) -> None:
        cid = self.campaign.id
        await self.memory.record_name_sources(
            GUILD, cid, [self.tamsin], upload_key="up1", label=LORE
        )
        self.assertEqual(await self.memory.lore_origin(GUILD, cid, self.tamsin), (LORE, ""))
        self.assertEqual(await self.memory.name_sources(GUILD, cid, self.tamsin), [("up1", LORE)])

    async def test_a_long_heading_is_cut_to_the_column(self) -> None:
        cid = self.campaign.id
        await self.memory.record_name_sources(
            GUILD,
            cid,
            [self.tamsin],
            upload_key="up1",
            label=LORE,
            headings={self.tamsin: "x" * 90},
        )
        origin = await self.memory.lore_origin(GUILD, cid, self.tamsin)
        self.assertEqual(origin, (LORE, "x" * 60))

    async def test_a_merged_away_entry_s_heading_reads_as_the_kept_one_s(self) -> None:
        cid = self.campaign.id
        other = await self.person("Tamsin")
        await self.memory.record_name_sources(
            GUILD, cid, [other], upload_key="up1", label=LORE, headings={other: "Brindle"}
        )
        await self.memory.merge(GUILD, cid, self.tamsin, other, source=DM_SOURCE, dm_said_same=True)
        self.assertEqual(await self.memory.lore_origin(GUILD, cid, self.tamsin), (LORE, "Brindle"))

    async def test_a_list_split_into_names_keeps_the_heading_on_each(self) -> None:
        cid = self.campaign.id
        written = await self.memory.add_entity(
            GUILD, cid, type="place", name="Aldmoor, Quillgate, Fenwick", source=DM_SOURCE,
            status="proposed",
        )  # fmt: skip
        listed = written.value.id
        await self.memory.record_name_sources(
            GUILD, cid, [listed], upload_key="up6", label=LORE, headings={listed: "Old Roads"}
        )
        split = await self.memory.split_waiting_name(GUILD, cid, listed, source=DM_SOURCE)
        data = await self.memory.lookup_data(GUILD, cid)
        parts = [e.id for e in data.entities if e.name in split.value.made]
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertEqual(await self.memory.lore_origin(GUILD, cid, part), (LORE, "Old Roads"))

    async def test_another_campaign_s_heading_is_never_read(self) -> None:
        second = await self.campaigns.create(GUILD, "Another", DM)
        written = await self.memory.add_entity(
            GUILD, second.id, type="character", name="Tamsin Reedwell", source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        theirs = written.value.id
        await self.memory.record_name_sources(
            GUILD, second.id, [theirs], upload_key="up3", label=LORE, headings={theirs: "Brindle"}
        )
        self.assertIsNone(await self.memory.lore_origin(GUILD, self.campaign.id, theirs))


class TheLoreRead(unittest.IsolatedAsyncioTestCase):
    async def test_the_headings_go_with_the_label(self) -> None:
        memory: Any = SimpleNamespace(record_name_sources=AsyncMock(return_value=1))
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        await name_background._record_sources(
            memory, campaign, {"character": ["a" * 32]}, Upload("text", "Notes", True), LORE,
            {"a" * 32: "Brindle"},
        )  # fmt: skip
        self.assertEqual(
            memory.record_name_sources.await_args.kwargs["headings"], {"a" * 32: "Brindle"}
        )


if __name__ == "__main__":
    unittest.main()
