"""Character sheets in the database (#723): one per character per campaign, never
shared, deleted with the character or the campaign, and carried in backups."""

from __future__ import annotations

import json
from typing import Any

from psycopg import errors as pg_errors

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory import sheets
from dmbot.memory.sheet_refresh import refresh
from dmbot.memory.sheet_store import SheetRefused, SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest
from tests.test_sheets import answer

PLAYER = 9
CHARACTER = 12345678


class SheetTest(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sheets = SheetStore(self.db)
        self.pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        self.sheet_data = sheets.parse(answer())

    async def linked(self, campaign: str | None = None) -> None:
        await self.sheets.link(GUILD_A, campaign or self.c, self.pc, CHARACTER)


class Linking(SheetTest):
    async def test_link_then_a_snapshot(self) -> None:
        await self.linked()
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((sheet.name, sheet.played_by), ("Testa", PLAYER))
        self.assertEqual(
            (sheet.url, sheet.character, sheet.sheet),
            (sheets.sheet_url(CHARACTER), CHARACTER, None),
        )
        url = sheets.sheet_url(CHARACTER)
        self.assertTrue(
            await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        )
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((sheet.sheet, sheet.fetched_at), (self.sheet_data, 5))

    async def test_a_snapshot_for_a_link_that_changed_meanwhile_is_dropped(self) -> None:
        await self.linked()
        old = sheets.sheet_url(CHARACTER)
        await self.sheets.link(GUILD_A, self.c, self.pc, 999)  # relinked while reading
        self.assertFalse(
            await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=old)
        )
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertIsNone(sheet.sheet)

    async def test_relinking_another_character_drops_the_old_snapshot(self) -> None:
        await self.linked()
        url = sheets.sheet_url(CHARACTER)
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        await self.linked()  # the same link again: kept
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertIsNotNone(sheet.sheet)
        await self.sheets.link(GUILD_A, self.c, self.pc, 999)
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((sheet.character, sheet.sheet, sheet.fetched_at), (999, None, None))

    async def test_the_typed_fallback_replaces_a_link(self) -> None:
        await self.linked()
        typed = sheets.typed("Testa", species="Elf", class_name="Bard", level=2, names=["Song"])
        assert typed is not None
        self.assertTrue(await self.sheets.save(GUILD_A, self.c, self.pc, typed, 6))
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((sheet.url, sheet.sheet), (None, typed))

    async def test_unlinking(self) -> None:
        await self.linked()
        self.assertTrue(await self.sheets.unlink(GUILD_A, self.c, self.pc))
        self.assertFalse(await self.sheets.unlink(GUILD_A, self.c, self.pc))
        self.assertEqual(await self.sheets.sheets(GUILD_A, self.c), [])

    async def test_only_a_player_character_with_a_player(self) -> None:
        npc = await self.add("Belleros")
        with self.assertRaises(SheetRefused):
            await self.sheets.link(GUILD_A, self.c, npc, CHARACTER)
        with self.assertRaises(SheetRefused):
            await self.sheets.link(GUILD_A, self.c, "0" * 32, CHARACTER)

    async def test_the_database_refuses_anything_but_a_character_link(self) -> None:
        with self.assertRaises(pg_errors.CheckViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO character_sheets"
                    " (guild_id, campaign_id, entity_id, url, player_id)"
                    " VALUES (%s, %s, %s, 'https://evil.example/characters/1', %s)",
                    (GUILD_A, self.c, self.pc, PLAYER),
                )


class Size(SheetTest):
    async def test_the_biggest_snapshot_clean_allows_fits_the_database(self) -> None:
        names = ["\u0928" * 55 + str(i) for i in range(100)]  # three bytes a letter
        biggest = sheets.clean(
            {
                "v": 1,
                "source": "typed",
                "name": "X",
                "languages": names,
                **{k: names for k in ("spells", "features", "feats", "items")},
            }
        )
        assert biggest is not None
        self.assertTrue(await self.sheets.save(GUILD_A, self.c, self.pc, biggest, 5))


class Finding(SheetTest):
    async def test_a_players_characters_in_the_server_or_one_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Another", DM)).id
        pc2 = await self.add("Testa Two", campaign=other, type="player_character", played_by=PLAYER)
        await self.add("Someone", type="player_character", played_by=PLAYER + 1)
        found = await self.sheets.characters_of(GUILD_A, PLAYER)
        self.assertEqual(
            [(c.campaign_name, c.name) for c in found],
            [("Another", "Testa Two"), ("Frozen Wastes", "Testa")],
        )
        (only,) = await self.sheets.characters_of(GUILD_A, PLAYER, other)
        self.assertEqual(only.entity_id, pc2)
        self.assertEqual(await self.sheets.characters_of(GUILD_B, PLAYER), [])

    async def test_one_characters_sheet(self) -> None:
        self.assertIsNone(await self.sheets.sheet(GUILD_A, self.c, self.pc))
        await self.linked()
        found = await self.sheets.sheet(GUILD_A, self.c, self.pc)
        assert found is not None
        self.assertEqual(found.character, CHARACTER)


class Merging(SheetTest):
    async def test_a_sheet_goes_with_the_character_it_was_merged_into(self) -> None:
        await self.linked()
        kept = await self.add("Testa the Bold", type="player_character", played_by=PLAYER)
        await self.memory.merge(GUILD_A, self.c, kept, self.pc, source="dm", dm_said_same=True)
        (moved,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((moved.entity_id, moved.character), (kept, CHARACTER))

    async def test_a_hidden_sheet_on_the_kept_one_never_beats_a_visible_one(self) -> None:
        await self.linked()  # the merged one's: visible, its player's
        kept = await self.add("Testa the Bold", type="player_character", played_by=PLAYER + 1)
        await self.sheets.link(GUILD_A, self.c, kept, 999)  # a sheet of the other player's
        async with self.db.guild(GUILD_A) as conn:  # the kept one changes hands, then merges
            await conn.execute(
                "UPDATE memory_entities SET played_by = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (PLAYER, GUILD_A, self.c, kept),
            )
        await self.memory.merge(GUILD_A, self.c, kept, self.pc, source="dm", dm_said_same=True)
        (left,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((left.entity_id, left.character), (kept, CHARACTER))  # the visible one

    async def test_the_kept_characters_own_sheet_stays(self) -> None:
        await self.linked()
        kept = await self.add("Testa the Bold", type="player_character", played_by=PLAYER)
        await self.sheets.link(GUILD_A, self.c, kept, 999)
        await self.memory.merge(GUILD_A, self.c, kept, self.pc, source="dm", dm_said_same=True)
        (left,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((left.entity_id, left.character), (kept, 999))
        self.assertEqual(await self.count("character_sheets"), 1)  # the merged one's forgotten


class PlayersOwn(SheetTest):
    async def test_a_player_changes_only_a_character_they_play(self) -> None:
        with self.assertRaises(SheetRefused):
            await self.sheets.link(GUILD_A, self.c, self.pc, CHARACTER, player=PLAYER + 1)
        await self.sheets.link(GUILD_A, self.c, self.pc, CHARACTER, player=PLAYER)
        self.assertIsNone(await self.sheets.sheet(GUILD_A, self.c, self.pc, player=PLAYER + 1))
        self.assertFalse(await self.sheets.unlink(GUILD_A, self.c, self.pc, player=PLAYER + 1))
        typed = sheets.typed("Testa", species="Elf", class_name="Bard", level=2, names=[])
        assert typed is not None
        with self.assertRaises(SheetRefused):
            await self.sheets.save(GUILD_A, self.c, self.pc, typed, 5, player=PLAYER + 1)
        self.assertTrue(await self.sheets.unlink(GUILD_A, self.c, self.pc, player=PLAYER))

    async def test_a_sheet_on_an_entry_with_no_player_is_left_out(self) -> None:
        await self.linked()
        async with self.db.guild(GUILD_A) as conn:  # as an undone merge can leave it
            await conn.execute(
                "UPDATE memory_entities SET played_by = NULL, type = 'npc'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.c, self.pc),
            )
        self.assertEqual(await self.sheets.sheets(GUILD_A, self.c), [])
        backup = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)  # not refused
        self.assertEqual(await self.count("character_sheets", GUILD_B), 0)
        self.assertIsNotNone(restored)


class ChangingHands(SheetTest):
    async def reassign(self, to: int | None) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET played_by = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (to, GUILD_A, self.c, self.pc),
            )

    async def test_the_next_player_never_sees_the_last_ones_sheet(self) -> None:
        await self.linked()
        url = sheets.sheet_url(CHARACTER)
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        await self.reassign(PLAYER + 1)
        self.assertEqual(await self.sheets.sheets(GUILD_A, self.c), [])
        self.assertIsNone(await self.sheets.sheet(GUILD_A, self.c, self.pc, player=PLAYER + 1))
        backup = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.sheets.sheets(GUILD_B, restored.id), [])
        self.assertEqual(await self.count("character_sheets", GUILD_B), 0)  # not just hidden
        # The new player links their own: nothing of the last one's stays.
        await self.sheets.link(GUILD_A, self.c, self.pc, CHARACTER, player=PLAYER + 1)
        (theirs,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertEqual((theirs.played_by, theirs.sheet), (PLAYER + 1, None))

    async def test_the_same_after_an_undo_then_a_reassignment(self) -> None:
        await self.linked()
        await self.reassign(None)  # as an undone merge leaves it
        await self.reassign(PLAYER + 1)
        self.assertEqual(await self.sheets.sheets(GUILD_A, self.c), [])
        await self.reassign(PLAYER)  # back to its own player: theirs again
        self.assertEqual(len(await self.sheets.sheets(GUILD_A, self.c)), 1)


class Isolation(SheetTest):
    async def test_the_same_link_in_two_campaigns_is_two_sheets(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        pc2 = await self.add("Testa", campaign=other, type="player_character", played_by=PLAYER)
        await self.linked()
        await self.sheets.link(GUILD_A, other, pc2, CHARACTER)
        url = sheets.sheet_url(CHARACTER)
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        (here,), (there,) = (
            await self.sheets.sheets(GUILD_A, self.c),
            await self.sheets.sheets(GUILD_A, other),
        )
        self.assertIsNotNone(here.sheet)
        self.assertIsNone(there.sheet)  # never copied across
        await self.sheets.unlink(GUILD_A, other, pc2)
        self.assertEqual(len(await self.sheets.sheets(GUILD_A, self.c)), 1)

    async def test_another_server_sees_nothing(self) -> None:
        await self.linked()
        self.assertEqual(await self.sheets.sheets(GUILD_B, self.c), [])
        self.assertFalse(await self.sheets.unlink(GUILD_B, self.c, self.pc))
        with self.assertRaises(SheetRefused):
            await self.sheets.link(GUILD_B, self.c, self.pc, CHARACTER)

    async def test_deleted_with_the_campaign(self) -> None:
        await self.linked()
        await self.campaigns.delete(GUILD_A, self.c)
        self.assertEqual(await self.count("character_sheets"), 0)


class Refreshing(SheetTest):
    async def test_a_refresh_keeps_the_old_snapshot_when_reading_fails(self) -> None:
        await self.linked()
        calls: list[int] = []

        async def ok(character: int) -> dict[str, Any]:
            calls.append(character)
            return self.sheet_data

        found = await refresh(self.sheets, GUILD_A, self.c, 5, fetch=ok)
        self.assertEqual((calls, found[0].sheet), ([CHARACTER], self.sheet_data))

        async def refused(character: int) -> dict[str, Any]:
            raise sheets.SheetError(sheets.NOT_PUBLIC, refused=True)

        with self.assertLogs("dmbot.memory.sheet_refresh", "INFO") as logs:
            found = await refresh(self.sheets, GUILD_A, self.c, 6, fetch=refused)
        self.assertEqual((found[0].sheet, found[0].fetched_at), (self.sheet_data, 5))
        self.assertIn(self.pc, logs.output[0])
        self.assertNotIn(str(CHARACTER), logs.output[0])  # ids of entries only


class Backups(SheetTest):
    async def test_the_link_and_snapshot_survive_backup_and_restore(self) -> None:
        await self.linked()
        url = sheets.sheet_url(CHARACTER)
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        (copy,) = await self.sheets.sheets(GUILD_B, restored.id)
        self.assertEqual((copy.url, copy.sheet, copy.fetched_at), (url, self.sheet_data, 5))

    async def edited(self, change: dict[str, Any]) -> dict[str, Any]:
        await self.linked()
        url = sheets.sheet_url(CHARACTER)
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        backup = await self.campaigns.export(GUILD_A, self.c)
        memory = backup["sections"]["memory"]
        (row,) = [r for r in memory if r["table"] == "sheet"]
        row.update(change)
        return backup

    async def test_smuggled_text_in_a_snapshot_is_dropped(self) -> None:
        snapshot = json.loads(json.dumps(self.sheet_data))
        snapshot["notes"] = "LEAK: rules text"
        snapshot["spells"].append("LEAK " * 40)
        backup = await self.edited({"sheet": snapshot})
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        (copy,) = await self.sheets.sheets(GUILD_B, restored.id)
        self.assertNotIn("LEAK", json.dumps(copy.sheet))

    async def test_a_snapshot_from_another_version_keeps_the_link(self) -> None:
        backup = await self.edited({"sheet": {"v": 99, "name": "From the future"}})
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        (copy,) = await self.sheets.sheets(GUILD_B, restored.id)
        self.assertEqual((copy.url, copy.sheet), (sheets.sheet_url(CHARACTER), None))

    async def test_a_bad_link_in_a_backup_is_refused(self) -> None:
        for change in (
            {"url": "https://evil.example/characters/1"},
            {"sheet": {"v": 1, "name": "x"}},
            {"source": "typed"},
            {"player_id": None},
            {"player_id": 0},
            {"player_id": "1"},
        ):
            backup = await self.edited(change)
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, backup, DM)
            await self.sheets.unlink(GUILD_A, self.c, self.pc)


class Backfill0029(DatabaseTest):
    """Migrations run with no server set: 0029's backfill must open every table it
    reads (character_sheets and memory_entities), or it deletes every sheet."""

    async def test_sheets_from_before_0029_belong_to_whoever_plays_them(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0029"]
        await self.db.close()  # start again from a schema as it was before 0029
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        memory = MemoryStore(self.db, clock=lambda: 1)
        c = (await campaigns.create(GUILD_A, "Frozen Wastes", DM)).id
        played = await memory.add_entity(
            GUILD_A, c, name="Testa", type="player_character", source="dm", played_by=PLAYER
        )
        unplayed = await memory.add_entity(GUILD_A, c, name="Belleros", type="npc", source="dm")
        async with self.db.guild(GUILD_A) as conn:
            for entity in (played.value.id, unplayed.value.id):
                await conn.execute(
                    "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url)"
                    " VALUES (%s, %s, %s, %s)",
                    (GUILD_A, c, entity, sheets.sheet_url(CHARACTER)),
                )
        await self.db.migrate()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT entity_id, player_id FROM character_sheets")
            rows = {r["entity_id"]: r["player_id"] for r in await cur.fetchall()}
        self.assertEqual(rows, {played.value.id: PLAYER})  # kept, with its player
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # no opening left
