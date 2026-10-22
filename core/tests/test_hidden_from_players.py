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

    async def test_a_players_character_can_never_be_hidden(self) -> None:
        pc = await self.add("Cerric", type="character", role="player_character", played_by=5)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_hidden_from_players(GUILD_A, self.c, pc, True, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_entity(
                GUILD_A, self.c, type="character", name="Kesh", source="dm",
                role="player_character", hidden_from_players=True,
            )  # fmt: skip
        await self.memory.set_hidden_from_players(GUILD_A, self.c, pc, False, source="dm")

    async def test_the_recap_doors_keep_their_pinned_search_path(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT proname, proconfig::text AS cfg, prosecdef FROM pg_proc"
                " WHERE proname IN ('dmbot_recap_check_names', 'dmbot_recap_shared')"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 2)  # (one pair for each test schema)
        for row in rows:
            self.assertIn("search_path", row["cfg"] or "", row["proname"])
            self.assertTrue(row["prosecdef"], row["proname"])

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

        def names_of(roster: list[Any]) -> set[str]:
            return {e.name for e in roster}

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

    async def becomes_a_player_character_and_is_shown(self, entry: str) -> None:
        self.assertFalse((await self.entity(entry)).hidden_from_players)

    async def test_an_entry_that_becomes_a_players_character_is_shown_on_every_path(self) -> None:
        """A player's character is named to everyone, so each way of becoming one clears the
        flag in the same write (one database trigger covers them all)."""
        for how in ("kind", "plays", "party"):
            hidden = await self.add(f"Hidden {how}", type="character", role="npc")
            await self.memory.set_hidden_from_players(GUILD_A, self.c, hidden, True, source="dm")
            if how == "kind":
                await self.memory.set_entity_type(
                    GUILD_A, self.c, hidden, "character", source="dm", role="player_character"
                )
            elif how == "plays":  # (the import and the table list set the player the same way)
                async with self.db.guild(GUILD_A) as conn:
                    await conn.execute(
                        "UPDATE memory_entities SET played_by = 5 WHERE id = %s", (hidden,)
                    )
            else:
                async with self.db.guild(GUILD_A) as conn:
                    await conn.execute(
                        "UPDATE memory_entities SET party = true WHERE id = %s", (hidden,)
                    )
            await self.becomes_a_player_character_and_is_shown(hidden)

    async def test_a_merge_into_a_players_character_is_shown(self) -> None:
        pc = await self.add("Cerric", type="character", role="player_character", played_by=7)
        await self.memory.merge(GUILD_A, self.c, pc, self.shadow, source="dm", dm_said_same=True)
        await self.becomes_a_player_character_and_is_shown(pc)

    async def test_a_hidden_entrys_names_are_all_secret_in_a_lookup(self) -> None:
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        mine = [n for n in lookup.names if n.entity_id == self.shadow]
        self.assertTrue(mine)
        self.assertTrue(all(n.secret for n in mine))
        self.assertFalse(any(n.secret for n in lookup.names if n.entity_id == self.jack))

    async def test_a_card_for_someone_who_may_not_see_secrets_is_nothing(self) -> None:
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertIsNone(name_card.card_text(lookup, self.shadow, [], secrets=False))
        self.assertIsNotNone(name_card.card_text(lookup, self.shadow, [], secrets=True))
        self.assertIn(
            "Hidden from players", name_card.card_text(lookup, self.shadow, [], secrets=True) or ""
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
        self.assertTrue(view.hidden)  # a name the DM adds starts unrevealed (#2404)
        self.assertEqual(view.hide.label, names.PLAYERS_DONT_KNOW)
        self.assertIsInstance(view.hide, discord.ui.Button)


if __name__ == "__main__":
    unittest.main()


class ThePresser(unittest.IsolatedAsyncioTestCase):
    """The card's button re-checks who presses it: it is the only gate, because the handler
    always writes as the DM."""

    async def press(self, user_id: int) -> tuple[Any, Any]:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        card = name_card.NameCard("c" * 32, "e" * 32, hide=False)
        memory = MagicMock()
        memory.entity = AsyncMock(return_value=SimpleNamespace(hidden_from_players=False))
        memory.set_hidden_from_players = AsyncMock()
        told = AsyncMock()
        campaign = SimpleNamespace(guild_id=1, id="c" * 32, dm_user_ids=frozenset({7}))
        interaction = MagicMock()
        interaction.user.id = user_id
        with (
            patch.object(name_card, "_answer_first", AsyncMock()),
            patch.object(name_card, "_current", AsyncMock(return_value=(campaign, "Mira"))),
            patch.object(name_card, "_memory", lambda _it: memory),
            patch.object(name_card, "_tell", told),
            patch.object(name_card, "changed", MagicMock()),
            patch.object(name_card, "show_card", AsyncMock()),
        ):
            await card._flip_hidden(interaction)
        return told, memory.set_hidden_from_players

    async def test_someone_who_may_not_see_secrets_changes_nothing(self) -> None:
        told, write = await self.press(99)
        write.assert_not_awaited()
        self.assertEqual(told.await_args.args[1], "Only the DM can change that.")

    async def test_a_dm_hides_it(self) -> None:
        told, write = await self.press(7)
        write.assert_awaited_once()
        self.assertEqual(write.await_args.args[3], True)
        told.assert_not_awaited()
