"""Maps put into words for MapBot (#2450, part 3): only maps are kept, per campaign and server,
newest first with the oldest dropped over the cap, deleted with the campaign and with the poster's
own lines, and carried in backups. Invented names only."""

from __future__ import annotations

import copy
import unittest
from typing import Any, ClassVar

from dmbot import images
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.images import Place, Read
from dmbot.map_notes import KEEP, MapNotesSection, MapNoteStore
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1, 2
DM, ME, FRIEND = 7, 8, 9


def a_map(name: str = "Harrowmere") -> Read:
    return Read(
        images.MAP,
        f"A map of {name}.",
        "",
        (Place(name, "on the coast"), Place("Old Mill", "two miles north of " + name)),
        "A coast road runs east to west.",
    )


class Kept(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MapNotesSection())
        self.store = MapNoteStore(self.db)
        self.one = await self.campaigns.create(GUILD_A, "One", DM)
        self.two = await self.campaigns.create(GUILD_A, "Two", DM)
        self.far = await self.campaigns.create(GUILD_B, "Far", DM)

    async def test_a_map_is_kept_with_its_places_and_who_posted_it(self) -> None:
        self.assertTrue(await self.store.add(GUILD_A, self.one.id, ME, a_map()))
        [note] = await self.store.notes(GUILD_A, self.one.id)
        self.assertEqual((note.user_id, note.alt), (ME, "A map of Harrowmere."))
        self.assertEqual(note.places[1], Place("Old Mill", "two miles north of Harrowmere"))
        self.assertEqual(note.layout, "A coast road runs east to west.")

    async def test_only_a_map_with_something_in_it_is_kept(self) -> None:
        self.assertFalse(await self.store.add(GUILD_A, self.one.id, ME, Read("picture", "A cat.")))
        self.assertFalse(
            await self.store.add(GUILD_A, self.one.id, ME, Read("text", "A note.", "hi"))
        )
        self.assertFalse(await self.store.add(GUILD_A, self.one.id, ME, Read("map", "Empty map.")))
        self.assertEqual(await self.store.notes(GUILD_A, self.one.id), [])

    async def test_each_campaign_and_server_sees_only_its_own(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map("Alpha"))
        await self.store.add(GUILD_A, self.two.id, ME, a_map("Beta"))
        await self.store.add(GUILD_B, self.far.id, ME, a_map("Gamma"))
        for guild, campaign, name in (
            (GUILD_A, self.one.id, "Alpha"),
            (GUILD_A, self.two.id, "Beta"),
            (GUILD_B, self.far.id, "Gamma"),
        ):
            [note] = await self.store.notes(guild, campaign)
            self.assertIn(name, note.alt)
        # another server cannot read a campaign's notes even when asking for its id
        self.assertEqual(await self.store.notes(GUILD_B, self.one.id), [])

    async def test_over_the_cap_the_oldest_goes(self) -> None:
        for n in range(KEEP + 3):
            await self.store.add(GUILD_A, self.one.id, ME, a_map(f"Town{n}"))
        notes = await self.store.notes(GUILD_A, self.one.id)
        self.assertEqual(len(notes), KEEP)
        self.assertIn(f"Town{KEEP + 2}", notes[0].alt)  # newest first
        self.assertNotIn("Town0", " ".join(n.alt for n in notes))

    async def test_they_go_with_the_campaign(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map())
        await self.campaigns.delete(GUILD_A, self.one.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM map_notes")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_they_go_with_the_posters_own_lines_and_no_one_elses(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map("Mine"))
        await self.store.add(GUILD_A, self.one.id, FRIEND, a_map("Theirs"))
        await self.store.add(GUILD_B, self.far.id, ME, a_map("Elsewhere"))
        await TranscriptStore(self.db).delete_person(GUILD_A, ME)
        [left] = await self.store.notes(GUILD_A, self.one.id)
        self.assertIn("Theirs", left.alt)
        self.assertEqual(len(await self.store.notes(GUILD_B, self.far.id)), 1)  # another server

    async def test_edge_values_are_kept_in_a_form_a_backup_can_restore(self) -> None:
        odd = Read(
            images.MAP,
            "   ",  # the AI said nothing useful for the short description
            "",
            (Place("  ", "nowhere"), Place("Harrowmere", ""), Place("Old Mill", "x" * 5000)),
            "",
        )
        self.assertTrue(await self.store.add(GUILD_A, self.one.id, ME, odd))
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        await self.campaigns.delete(GUILD_A, self.one.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        [note] = await self.store.notes(GUILD_B, restored.id)
        self.assertEqual(note.alt, "A map.")
        self.assertEqual([p.name for p in note.places], ["Harrowmere", "Old Mill"])

    async def test_a_map_with_only_blank_places_and_no_layout_is_not_kept(self) -> None:
        blank = Read(images.MAP, "A map.", "", (Place("  ", "x"),), "  ")
        self.assertFalse(await self.store.add(GUILD_A, self.one.id, ME, blank))

    async def test_adds_at_once_keep_the_cap_exact(self) -> None:
        import asyncio

        await asyncio.gather(
            *(self.store.add(GUILD_A, self.one.id, ME, a_map(f"T{n}")) for n in range(KEEP + 6))
        )
        self.assertEqual(len(await self.store.notes(GUILD_A, self.one.id)), KEEP)

    async def test_one_poster_and_one_message_can_keep_several_maps(self) -> None:
        for name in ("Alpha", "Beta", "Gamma"):
            await self.store.add(GUILD_A, self.one.id, ME, a_map(name))
        self.assertEqual(len(await self.store.notes(GUILD_A, self.one.id)), 3)

    async def test_a_hostile_backup_section_is_refused_and_nothing_is_written(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        backup = copy.deepcopy(backup)
        sections = backup.get("sections", backup)
        sections["map_notes"] = [{"by": "0", "at": 1, "alt": "x", "layout": "", "places": []}]
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, backup, DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM map_notes")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_deleted_pictures_notes_go_and_no_one_elses(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map("Mine"), message_id=500)
        await self.store.add(GUILD_A, self.one.id, ME, a_map("Other"), message_id=501)
        await self.store.add(GUILD_B, self.far.id, ME, a_map("Far"), message_id=500)  # same id
        self.assertEqual(await self.store.forget_message(GUILD_A, 500), 1)
        left = await self.store.notes(GUILD_A, self.one.id)
        self.assertEqual([n.alt for n in left], ["A map of Other."])
        self.assertEqual(len(await self.store.notes(GUILD_B, self.far.id)), 1)
        self.assertEqual(await self.store.forget_message(GUILD_A, 0), 0)  # 0 is "no message"

    async def test_a_backup_carries_no_message_id(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map(), message_id=777)
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        self.assertNotIn("777", str(backup.get("sections", backup)).replace("map_notes", ""))
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.store.forget_message(GUILD_B, 777), 0)  # nothing to match
        self.assertEqual(len(await self.store.notes(GUILD_B, restored.id)), 1)

    async def test_they_travel_in_a_backup(self) -> None:
        await self.store.add(GUILD_A, self.one.id, ME, a_map("Alpha"))
        await self.store.add(GUILD_A, self.one.id, FRIEND, a_map("Beta"))
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        await self.campaigns.delete(GUILD_A, self.one.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        got = await self.store.notes(GUILD_B, restored.id)
        self.assertEqual(sorted(n.alt for n in got), ["A map of Alpha.", "A map of Beta."])
        self.assertEqual(sorted(n.user_id for n in got), [ME, FRIEND])


class DamagedBackups(unittest.TestCase):
    good: ClassVar[dict[str, Any]] = {
        "by": "8",
        "at": 5,
        "alt": "A map.",
        "layout": "Roads.",
        "places": [{"name": "Harrowmere", "where": "on the coast"}],
    }

    def test_a_damaged_row_is_refused(self) -> None:
        section = MapNotesSection()
        bad_rows = []
        for change in (
            {"extra": 1},
            {"by": "0"},
            {"by": 8},
            {"at": True},
            {"alt": ""},
            {"alt": "x" * 401},
            {"layout": "x" * 2501},
            {"places": "none"},
            {"places": [{"name": ""}]},
            {"places": [{"name": "A", "where": 3}]},
            {"places": [], "layout": ""},
            {"places": [{"name": "A", "where": "b"}] * (images.PLACES_MAX + 1)},
        ):
            bad_rows.append({**copy.deepcopy(self.good), **change})
        for row in bad_rows:
            with self.subTest(row), self.assertRaises(CampaignError):
                section.check([row])
        with self.assertRaises(CampaignError):
            section.check(["text"])
        with self.assertRaises(CampaignError):
            section.check([self.good] * (KEEP + 1))
        self.assertEqual(len(section.check([self.good])), 1)
