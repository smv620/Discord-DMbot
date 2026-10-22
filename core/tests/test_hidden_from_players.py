"""The DM's "Players don't know this one yet" (#2321): any entry kept back from players like a
clue only some know, on the store, in recall and the NPC list, on lookups, in backups, and the
two doors where the DM sets it (Add a name, the name card)."""

from __future__ import annotations

import unittest
from typing import Any

import discord

from dmbot import schema
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.ui import name_card, names
from tests.test_memory_store import GUILD_A, GUILD_B, ConfirmedMemoryTest


class Hidden(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.jack = await self.add("Jack", type="character", role="npc")
        self.guild = await self.add("The Thieves Guild", type="faction")
        self.shadow = await self.add("The Shadow", type="character", role="npc")
        await self.relate(self.jack, "member_of", self.guild, status="confirmed")
        await self.relate(self.shadow, "member_of", self.guild, status="confirmed")
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.shadow, True, source="dm")

    async def entity(
        self, entity_id: str, guild: int = GUILD_A, campaign: str | None = None
    ) -> Any:
        return await self.memory.entity(guild, campaign or self.c, entity_id)

    async def test_only_the_dm_sets_it_and_it_can_be_turned_off(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_hidden_from_players(
                GUILD_A, self.c, self.jack, True, source="entitybot"
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_entity(
                GUILD_A, self.c, type="character", name="Sly", source="entitybot",
                hidden_from_players=True,
            )  # fmt: skip
        self.assertTrue((await self.entity(self.shadow)).hidden_from_players)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.shadow, False, source="dm")
        self.assertFalse((await self.entity(self.shadow)).hidden_from_players)

    async def test_an_entry_added_hidden_starts_hidden(self) -> None:
        written = await self.memory.add_entity(
            GUILD_A, self.c, type="character", name="Vex", source="dm", status="confirmed",
            hidden_from_players=True,
        )  # fmt: skip
        self.assertTrue(written.value.unseen_by_players)
        self.assertFalse((await self.entity(self.jack)).unseen_by_players)

    async def test_players_never_hear_of_it_and_the_dm_screen_does(self) -> None:
        players = await RecallService(self.memory).recall(
            GUILD_A, self.c, seeds=["The Thieves Guild"], audience=PLAYERS, hops=2, opted_in=set()
        )
        self.assertNotIn(self.shadow, {i.id for i in players.items})
        self.assertIn(self.jack, {i.id for i in players.items})
        dm = await RecallService(self.memory).recall(
            GUILD_A, self.c, seeds=["The Thieves Guild"], audience=DM_SCREEN, hops=2, opted_in=set()
        )
        self.assertIn(self.shadow, {i.id for i in dm.items})

    async def test_the_npc_list_leaves_it_out_until_the_players_know(self) -> None:
        names_of = lambda roster: {e.name for e in roster}  # noqa: E731
        service = RecallService(self.memory)
        got = names_of(await service.roster(GUILD_A, self.c))
        self.assertIn("Jack", got)
        self.assertNotIn("The Shadow", got)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.shadow, False, source="dm")
        self.assertIn("The Shadow", names_of(await service.roster(GUILD_A, self.c)))

    async def test_a_lookup_hides_it_from_players_and_not_from_the_dm(self) -> None:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        lookup = CampaignLookup.build(data)
        self.assertIn(self.shadow, lookup.entities)
        self.assertEqual(lookup.hidden, frozenset({self.shadow}))
        self.assertNotIn(self.shadow, lookup.for_viewer(secrets=False).entities)
        self.assertIn(self.shadow, lookup.for_viewer(secrets=True).entities)
        self.assertIn(self.jack, lookup.for_viewer(secrets=False).entities)

    async def test_it_comes_back_from_a_backup_and_an_old_backup_restores_as_known(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(
            GUILD_B, decode_backup(encode_backup(data)), 7
        )
        by_name = {e.name: e for e in await self.memory.entities(GUILD_B, restored.id)}
        self.assertTrue(by_name["The Shadow"].hidden_from_players)
        self.assertFalse(by_name["Jack"].hidden_from_players)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity":
                row.pop("hidden_from_players", None)
        old = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertFalse(
            any(e.hidden_from_players for e in await self.memory.entities(GUILD_B, old.id))
        )

    async def test_a_merge_keeps_it_hidden_if_either_was(self) -> None:
        keeper = await self.add("Shade", type="character", role="npc")
        await self.memory.merge(
            GUILD_A, self.c, keeper, self.shadow, source="dm", dm_said_same=True
        )
        self.assertTrue((await self.entity(keeper)).hidden_from_players)


class Doors(unittest.TestCase):
    def test_both_recap_doors_leave_it_out(self) -> None:
        self.assertIn("hidden_from_players", schema.HIDDEN_FROM_PLAYERS)
        for door in ("dmbot_recap_check_names", "dmbot_recap_shared"):
            self.assertIn(door, schema.HIDDEN_FROM_PLAYERS)

    def test_the_labels_name_the_state_and_have_no_colon(self) -> None:
        for label in (
            names.PLAYERS_KNOW,
            names.PLAYERS_DONT_KNOW,
            name_card.HIDE_BUTTON,
            name_card.SHOW_BUTTON,
        ):
            self.assertLessEqual(len(label), 25, label)
            self.assertNotIn(":", label)

    def test_only_a_dm_gets_the_switch_on_the_kind_step(self) -> None:
        self.assertFalse(hasattr(names.KindPicker("c" * 32, "Kesh", [], []), "hide"))
        view = names.KindPicker("c" * 32, "Kesh", [], [], can_hide=True)
        self.assertEqual(view.hide.label, names.PLAYERS_KNOW)
        self.assertIsInstance(view.hide, discord.ui.Button)


if __name__ == "__main__":
    unittest.main()
