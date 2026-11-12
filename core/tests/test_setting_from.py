"""Where the story is set, worked out from the shared adventure (#4173 slice 2b; Supervisor's
ruling on #4173, 2026-11-12): the names-only list, DMbot's guess and the DM's keep or no, the guess
placing nothing until kept, backups, and migration 0459 run over a campaign that has a setting.
Invented names, published titles and world names only."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory import setting_worlds as sw
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from dmbot.ui import name_card, place_size
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_names import DM as NAMES_DM
from tests.test_memory_names import GUILD as NAMES_GUILD
from tests.test_memory_names import MANAGER, NamesTest
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest


class TheList(unittest.TestCase):
    def test_a_title_in_the_files_name(self) -> None:
        for name in ("Rime of the Frostmaiden.pdf", "rime-of-the-frostmaiden (2020).PDF",
                     "Baldur's Gate: Descent into Avernus.docx"):  # fmt: skip
            with self.subTest(name=name):
                found = sw.world_for(name)
                assert found is not None
                self.assertEqual(found.place, "Faerûn")
        found = sw.world_for("Curse of Strahd.pdf")
        assert found is not None
        self.assertEqual(found.key, "ravenloft")

    def test_a_world_named_in_the_opening_words(self) -> None:
        found = sw.world_for("my notes.docx", "An adventure for the Forgotten Realms.")
        assert found is not None
        self.assertEqual(found.place, "Faerûn")
        late = "x " * 3000 + "Eberron"  # (only the opening is read)
        self.assertIsNone(sw.world_for("notes.pdf", late))

    def test_nothing_or_two_worlds_is_no_guess(self) -> None:
        self.assertIsNone(sw.world_for("session notes.pdf", "A town by the sea."))
        self.assertIsNone(sw.world_for("planes.pdf", "From Eberron to Greyhawk and back."))

    def test_the_list_holds_names_only(self) -> None:
        # (Supervisor's condition 1: no descriptions, places or text from the books)
        for world in sw.WORLDS:
            self.assertLessEqual(len(world.description), 25, world)
        for title in sw.ADVENTURES:
            self.assertLessEqual(len(title), 60, title)


class TheGuess(MemoryTest):
    async def guess(self, world: str = "Faerûn") -> str | None:
        written = await self.memory.guess_setting(GUILD_A, self.c, world, source="background")
        return written.value.id if written.value is not None else None

    async def place(self, entity_id: str):  # type: ignore[no-untyped-def]  # (an Entity)
        found = await self.memory.entity(GUILD_A, self.c, entity_id)
        assert found is not None
        return found

    async def test_a_guess_makes_a_hidden_world_marked_as_a_guess(self) -> None:
        made = await self.guess()
        assert made is not None
        faerun = await self.place(made)
        self.assertTrue(faerun.setting_guessed)
        self.assertEqual((faerun.place_level, faerun.hidden_from_players), ("world", True))

    async def test_never_over_the_dm_and_never_twice_after_a_no(self) -> None:
        own = await self.add("Golarion", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, own, "world", source=BY_DM)
        await self.memory.set_setting(GUILD_A, self.c, own, source=BY_DM)
        self.assertIsNone(await self.guess())  # (the DM's own setting stands)
        await self.memory.set_setting(GUILD_A, self.c, None, source=BY_DM)
        made = await self.guess()
        assert made is not None
        await self.memory.decline_setting(GUILD_A, self.c, made, source=BY_DM)
        self.assertIsNone(await self.guess("Khorvaire"))  # (✖ means never again)

    async def test_keeping_makes_it_the_dms_and_shows_it(self) -> None:
        made = await self.guess()
        assert made is not None
        kept = await self.memory.keep_setting(GUILD_A, self.c, made, source=BY_DM)
        self.assertEqual(kept.value.setting_from, BY_DM)
        self.assertFalse(kept.value.hidden_from_players)
        assert kept.batch is not None
        await self.memory.undo(GUILD_A, self.c, kept.batch)
        self.assertTrue((await self.place(made)).setting_guessed)

    async def test_only_the_dm_keeps_or_refuses(self) -> None:
        made = await self.guess()
        assert made is not None
        for answer in (self.memory.keep_setting, self.memory.decline_setting):
            with self.assertRaises(MemoryRuleError):
                await answer(GUILD_A, self.c, made, source="background")

    async def test_a_guess_places_nothing_until_kept(self) -> None:
        made = await self.guess()
        assert made is not None
        town = await self.add("Targos", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, town, "settlement", source=BY_DM)
        from dmbot.memory import states
        from dmbot.memory.store import _load_ontology

        async with self.memory._read(GUILD_A, self.c) as scope:
            nesting = await states.place_nesting(scope, await _load_ontology(scope))
        self.assertNotIn(made, nesting.above(town))
        await self.memory.keep_setting(GUILD_A, self.c, made, source=BY_DM)
        async with self.memory._read(GUILD_A, self.c) as scope:
            nesting = await states.place_nesting(scope, await _load_ontology(scope))
        self.assertIn(made, nesting.above(town))

    async def test_an_existing_place_of_that_name_is_used_when_big_enough(self) -> None:
        faerun = await self.add("Faerûn", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, faerun, "world", source=BY_DM)
        self.assertEqual(await self.guess(), faerun)
        self.assertEqual(len(await self.memory.entities(GUILD_A, self.c)), 1)

    async def test_a_backup_carries_the_guess_and_refuses_a_broken_pair(self) -> None:
        made = await self.guess()
        assert made is not None
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual(
            [(r["setting"], r["setting_from"]) for r in copy["memory_entities"]], [(True, "read")]
        )
        data = decode_backup(raw)
        assert isinstance(data, dict)
        for row in data["sections"]["memory"]:
            if row["table"] == "entity":
                row["setting_from"] = None  # (a setting that says nobody set it)
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_a_backup_from_before_says_the_dm_set_it(self) -> None:
        own = await self.add("Golarion", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, own, "world", source=BY_DM)
        await self.memory.set_setting(GUILD_A, self.c, own, source=BY_DM)
        data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
        assert isinstance(data, dict)
        for row in data["sections"]["memory"]:
            row.pop("setting_from", None)  # (written before 0459)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual([r["setting_from"] for r in copy["memory_entities"]], ["dm"])


class TheMigration(unittest.IsolatedAsyncioTestCase):
    """0459 run over a campaign that already has a setting: it becomes the DM's (CLAUDE.md, #704:
    a migration that changes rows opens row-level security for itself, proven on real rows)."""

    NAME = "0459_setting_from"

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != self.NAME),
        )  # fmt: skip
        self.c = (await CampaignStore(self.db).create(GUILD_A, "Frostmaiden", DM)).id

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def test_a_setting_made_before_is_the_dms(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            for name, is_setting in (("Faerûn", True), ("Targos", False)):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, place_level, place_level_from,"
                    " setting) VALUES (%s, %s, %s, 'place', %s, '', 'confirmed', 'dm', 1, %s,"
                    " 'dm', %s)",
                    (GUILD_A, self.c, uuid.uuid4().hex, name,
                     "world" if is_setting else "settlement", is_setting),
                )  # fmt: skip
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT name, setting, setting_from FROM memory_entities WHERE campaign_id = %s"
                " ORDER BY name",
                (self.c,),
            )
            rows = [(r["name"], r["setting"], r["setting_from"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [("Faerûn", True, "dm"), ("Targos", False, None)])
        self.assertTrue(MemoryStore(self.db))  # (the store opens on the migrated schema)


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        made = await self.memory.guess_setting(
            NAMES_GUILD, self.campaign.id, "Faerûn", source="background"
        )
        assert made.value is not None
        self.faerun = made.value.id

    async def card(self, user: int = NAMES_DM) -> tuple[str, Any]:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.faerun)
        sent = it.response.sent[0]
        return str(sent[0]), sent[1].get("view")

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children] if view else []

    async def test_the_dm_sees_the_guess_and_keeps_it(self) -> None:
        text, view = await self.card()
        self.assertIn("🌍 **Campaign setting?** DMbot guessed this from your adventure.", text)
        self.assertIn(place_size.KEEP_SETTING_BUTTON, self.labels(view))
        self.assertIn(place_size.NOT_SETTING_BUTTON, self.labels(view))
        self.assertNotIn(place_size.SETTING_OFF_BUTTON, self.labels(view))
        it = self.it()
        await view._keep_setting(it)
        self.assertIn("🌍 Noted: **Faerûn** is the campaign's setting.", it.response.edited[-1][0])
        text, view = await self.card()
        self.assertNotIn("guessed", text)
        self.assertIn(place_size.SETTING_OFF_BUTTON, self.labels(view))

    async def test_a_manager_who_is_not_a_dm_sees_no_guess_and_cannot_answer(self) -> None:
        text, view = await self.card(MANAGER)
        self.assertNotIn("Campaign setting", text)
        self.assertNotIn(place_size.KEEP_SETTING_BUTTON, self.labels(view))
        it = self.it(MANAGER)
        await place_size.answer_guess(it, self.campaign.id, self.faerun, keep=True)
        self.assertEqual(it.response.sent[-1][0], place_size.ONLY_SETTING_DM)


class AfterTheReview(MemoryTest):
    """The reviewer's cases on #4173 slice 2b."""

    async def test_keep_never_shows_a_place_someone_else_hid(self) -> None:
        faerun = await self.add("Faerûn", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, faerun, "world", source=BY_DM)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, faerun, True, source=BY_DM)
        await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        kept = await self.memory.keep_setting(GUILD_A, self.c, faerun, source=BY_DM)
        self.assertTrue(kept.value.hidden_from_players)

    async def test_the_dm_taking_a_guess_away_is_a_no(self) -> None:
        made = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert made.value is not None
        await self.memory.set_setting(GUILD_A, self.c, None, source=BY_DM)
        gone = await self.memory.entity(GUILD_A, self.c, made.value.id)
        assert gone is not None
        self.assertEqual((gone.setting, gone.setting_from), (False, "declined"))
        again = await self.memory.guess_setting(GUILD_A, self.c, "Khorvaire", source="background")
        self.assertIsNone(again.value)

    async def test_undo_of_a_no_brings_the_guess_back(self) -> None:
        made = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert made.value is not None
        no = await self.memory.decline_setting(GUILD_A, self.c, made.value.id, source=BY_DM)
        assert no.batch is not None
        await self.memory.undo(GUILD_A, self.c, no.batch)
        back = await self.memory.entity(GUILD_A, self.c, made.value.id)
        assert back is not None
        self.assertTrue(back.setting_guessed)

    async def test_the_flanaess_with_or_without_the(self) -> None:
        own = await self.add("Flanaess", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, own, "world", source=BY_DM)
        made = await self.memory.guess_setting(GUILD_A, self.c, "the Flanaess", source="background")
        assert made.value is not None
        self.assertEqual(made.value.id, own)


class TheListAgain(unittest.TestCase):
    def test_every_title_names_a_world_on_the_list(self) -> None:
        self.assertTrue(set(sw.ADVENTURES.values()) <= set(sw.BY_KEY))
        self.assertEqual(sw.World._fields, ("key", "label", "place", "description"))

    def test_a_legal_line_names_no_world(self) -> None:
        legal = "Forgotten Realms and the dragon ampersand are trademarks of their owner.\nA hut."
        self.assertIsNone(sw.world_for("community adventure.pdf", legal))
        self.assertIsNone(sw.world_for("x.pdf", "© 2024 someone. Eberron is theirs.\nA hut."))
