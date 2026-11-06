"""The players' line for an entry (#2967): the DM writes it, a player is told it only where the
gate shows them the entry and only if it names nothing they may not be told, and the DM's own note
never stands in for it. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import backup
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

LINE = "the lantern glows green when a monstrosity is near"


class TheLine(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lantern = await self.add("Greenlight", type="item")
        self.keep = await self.add("The Keep", type="place")
        await self.memory.add_relation(
            GUILD_A, self.c, self.lantern, "located_in", self.keep, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip

    async def set(self, text: str, entity: str | None = None, source: str = DM_SOURCE) -> Any:
        return await self.memory.set_players_text(
            GUILD_A, self.c, entity or self.lantern, text, source=source
        )

    async def says(self, audience: str) -> str:
        got = await RecallService(self.memory, None).recall(
            GUILD_A, self.c, seeds=["Greenlight"], audience=audience, opted_in=()
        )
        return " | ".join(i.text for i in got.items)

    async def entity(self, entity: str | None = None) -> Any:
        return next(
            e
            for e in await self.memory.entities(GUILD_A, self.c)
            if e.id == (entity or self.lantern)
        )

    async def test_the_dm_writes_it_and_a_player_is_told_it(self) -> None:
        await self.set(LINE)
        self.assertEqual((await self.entity()).players_text, LINE)
        self.assertIn(LINE, await self.says(PLAYERS))

    async def test_the_dms_note_never_stands_in_for_it(self) -> None:
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            from dmbot.memory._changes import ENTITIES

            await w.update(ENTITIES, self.lantern, {"description": "secret: it is cursed"})
        self.assertNotIn("cursed", await self.says(PLAYERS))  # no line, no fallback
        await self.set(LINE)
        players = await self.says(PLAYERS)
        self.assertIn(LINE, players)
        self.assertNotIn("cursed", players)
        dm = await self.says(DM_SCREEN)
        self.assertIn("cursed", dm)  # the DM screen keeps the DM's own note
        self.assertNotIn(LINE, dm)

    async def test_only_the_dm_may_write_it_and_too_long_is_refused_not_cut(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.set(LINE, source="extractor")
        with self.assertRaises(MemoryRuleError):
            await self.set("x" * 121)
        await self.set("x" * 120)  # exactly the limit is fine

    async def test_blank_clears_it_and_undo_gives_it_back(self) -> None:
        await self.set(LINE)
        blanked = await self.set("   ")
        self.assertIsNone((await self.entity()).players_text)
        self.assertNotIn(LINE, await self.says(PLAYERS))
        await self.memory.undo(GUILD_A, self.c, blanked.batch, source=DM_SOURCE)
        self.assertEqual((await self.entity()).players_text, LINE)

    async def test_an_entry_hidden_from_the_players_tells_them_no_line(self) -> None:
        hidden = await self.add("Veilstone", type="item", hidden_from_players=True)
        await self.set("it hums near the gate", hidden)
        got = await RecallService(self.memory, None).recall(
            GUILD_A, self.c, seeds=["Veilstone"], audience=PLAYERS, opted_in=()
        )
        self.assertNotIn("hums", " | ".join(i.text for i in got.items))

    async def test_a_line_naming_what_a_player_may_not_be_told_is_left_out_whole(self) -> None:
        await self.add("Lord Veil", type="character", role="npc", hidden_from_players=True)
        await self.set("sworn to guard the grave of Lord Veil")
        players = await self.says(PLAYERS)
        self.assertNotIn("Veil", players)
        self.assertNotIn("sworn", players)  # left out, not shown with a hole
        self.assertIn("Greenlight", players)  # the entry itself is still told
        await self.set("keeps the keep safe")
        self.assertIn("keeps the keep safe", await self.says(PLAYERS))

    async def test_a_line_may_name_the_entry_itself_and_a_known_one(self) -> None:
        await self.set("Greenlight from The Keep glows green")
        self.assertIn("Greenlight from The Keep glows", await self.says(PLAYERS))


class TheBackup(ConfirmedMemoryTest):
    def test_a_backup_file_may_carry_the_line_within_its_limit(self) -> None:
        self.assertTrue(backup._valid("players_text", "x" * 120))
        self.assertFalse(backup._valid("players_text", "x" * 121))
        self.assertTrue(backup._valid("players_text", None))
        self.assertFalse(backup._valid("players_text", 5))
        self.assertFalse(backup._valid("players_text", ""))  # the column holds NULL, never ""


class TheUndoLogAndOldFiles(ConfirmedMemoryTest):
    def test_the_undo_log_and_old_backup_files_know_the_column_is_empty_before_it(self) -> None:
        from dmbot.memory import _changes

        self.assertIn("players_text", _changes._ADDED_LATER["memory_entities"])  # a snapshot older
        self.assertIsNone(_changes._ADDED_LATER["memory_entities"]["players_text"])  # reads empty

    async def test_a_secret_spelling_in_the_line_is_left_out_for_players(self) -> None:
        lantern = await self.add("Greenlight", type="item")
        grey = await self.add("Kesh", type="character", role="npc")
        await self.memory.add_alias(
            GUILD_A, self.c, grey, "the Grey Fox", kind="nickname", source="dm", secret=True
        )
        await self.memory.set_players_text(
            GUILD_A, self.c, lantern, "owned by the Grey Fox", source=DM_SOURCE
        )
        got = await RecallService(self.memory, None).recall(
            GUILD_A, self.c, seeds=["Greenlight"], audience=PLAYERS, opted_in=()
        )
        self.assertNotIn("Grey Fox", " | ".join(i.text for i in got.items))


class OldFiles(unittest.TestCase):
    def row(self) -> dict[str, Any]:
        return {
            "table": "entity", "id": "a" * 32, "type": "item", "name": "Greenlight",
            "description": "", "status": "confirmed", "merged_into": None, "source": "dm",
            "created_at": 1, "played_by": None, "role": None, "needs_look": False,
            "unique_item": None, "audience_limited": False, "party": False,
            "hidden_from_players": False, "made_before_name_gate": False, "manipulated_by": None,
            "unnamed": False, "unnamed_anchor": None, "unnamed_role": None, "unnamed_what": None,
        }  # fmt: skip

    def test_a_backup_from_before_the_line_still_loads_with_none(self) -> None:
        by_tag = backup._checked_rows([self.row()])  # (no "players_text" key at all)
        self.assertIsNone(by_tag["entity"][0]["players_text"])

    def test_a_line_in_a_file_loads_and_an_empty_one_is_damage(self) -> None:
        self.assertEqual(
            backup._checked_rows([{**self.row(), "players_text": LINE}])["entity"][0][
                "players_text"
            ],
            LINE,
        )
        with self.assertRaises(CampaignError):
            backup._checked_rows([{**self.row(), "players_text": ""}])
