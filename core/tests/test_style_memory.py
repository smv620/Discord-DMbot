"""The initiative style a campaign keeps between sessions (#1784): only a DM's words are kept,
only when they change, the next session starts from it, and it goes in a backup. The store tests
need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot import fight_style, fightbot
from dmbot.bot import DMBot
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.fight_style import StyleState
from tests.pg import DatabaseTest
from tests.test_fight_style import DM, YES, Ground

GUILD_A, GUILD_B = 111, 222


class Encoding(unittest.TestCase):
    def test_what_is_kept_round_trips(self) -> None:
        for order in ("unknown", "each", "side", "reroll"):
            for declarations in ("unknown", "yes"):
                kept = fight_style.encode(order, declarations)
                self.assertEqual(fight_style.decode(kept), (order, declarations))
                self.assertTrue(kept is None or kept in fight_style.SAVED_STYLES)
        self.assertIsNone(fight_style.encode("unknown", "unknown"))
        self.assertEqual(fight_style.encode("side", "yes"), "side+declare")
        self.assertEqual(fight_style.decode("garbage"), ("unknown", "unknown"))
        self.assertEqual(fight_style.decode(None), ("unknown", "unknown"))

    def test_a_session_starts_from_what_was_kept(self) -> None:
        state = StyleState.from_saved("reroll+declare")
        self.assertEqual((state.order, state.declarations), ("reroll", "yes"))
        self.assertTrue(state.known)  # FightBot can time reminders from the first fight
        self.assertEqual(state.set_by, {"order": "saved", "declarations": "saved"})
        self.assertEqual(StyleState.from_saved(None).order, "unknown")

    def test_the_dm_line_says_last_time_until_something_is_heard_this_session(self) -> None:
        state = StyleState.from_saved("side")
        self.assertEqual(state.dm_phrase(), "Initiative, as heard last time: by side")
        state.vote("order", "side", DM, from_dm=True, consenting=YES)  # the DM says it again
        self.assertEqual(state.dm_phrase(), "Initiative, as heard: by side")
        fresh = StyleState.from_saved("each+declare")
        fresh.vote("order", "reroll", DM, from_dm=True, consenting=YES)
        self.assertEqual(
            fresh.dm_phrase(), "Initiative, as heard: rolled each round; actions declared first"
        )
        self.assertEqual(StyleState().dm_phrase(), "Initiative, as heard: not heard yet")

    def test_a_dm_line_overrides_and_players_need_two_who_agree(self) -> None:
        state = StyleState.from_saved("each")
        state.vote("order", "side", 1, from_dm=False, consenting=YES)
        self.assertEqual(state.order, "each")  # one player is not enough
        state.vote("order", "side", 2, from_dm=False, consenting=YES)
        self.assertEqual((state.order, state.set_by["order"]), ("side", "players"))
        again = StyleState.from_saved("each")
        again.vote("order", "side", DM, from_dm=True, consenting=YES)
        self.assertEqual((again.order, again.set_by["order"]), ("side", "dm"))
        for user in (1, 2, 3):  # once a DM has said, players no longer count
            again.vote("order", "each", user, from_dm=False, consenting=YES)
        self.assertEqual(again.order, "side")

    def test_only_a_dms_words_change_what_is_kept(self) -> None:
        state = StyleState.from_saved("each")
        self.assertEqual(state.to_keep("each"), "each")
        state.vote("order", "side", 1, from_dm=False, consenting=YES)
        state.vote("order", "side", 2, from_dm=False, consenting=YES)  # two players agree
        self.assertEqual(state.to_keep("each"), "each")  # the session's, not kept
        state.infer_no_declarations()
        self.assertEqual(state.to_keep("each"), "each")
        state.vote("order", "side", DM, from_dm=True, consenting=YES)
        self.assertEqual(state.to_keep("each"), "side")
        state.vote("declarations", "yes", DM, from_dm=True, consenting=YES)
        self.assertEqual(state.to_keep("each"), "side+declare")


class Remembering(Ground):
    """The bot keeps it when a DM's line changes it, and not otherwise."""

    def keeper(self, table: Any) -> Any:
        bot = MagicMock(spec=DMBot)
        bot.campaigns = SimpleNamespace(set_initiative_style=AsyncMock())
        bot._saved_calls = bot.campaigns.set_initiative_style
        return bot

    async def remember(self, bot: Any, table: Any) -> None:
        await DMBot._remember_style(bot, table)

    async def test_a_dm_line_saves_it_and_a_player_vote_does_not(self) -> None:
        table, _bot = self.primed([11, 12])
        keeper = self.keeper(table)
        for user in (11, 12):
            self.player_says(table, user, "side initiative")
        await self.remember(keeper, table)
        keeper._saved_calls.assert_not_awaited()  # two players agreed: this session only
        self.assertEqual(table.fight.style.order, "side")
        self.dm_says(table, "group initiative")
        await self.remember(keeper, table)
        keeper._saved_calls.assert_awaited_once_with(table.guild_id, table.campaign_id, "side")
        self.assertEqual(table.fight.saved, "side")

    async def test_a_guess_never_overrules_a_kept_style_but_a_stated_one_does(self) -> None:
        table = self.table()
        table.fight = fightbot.FightState.starting_from("side")
        table.dm_user_ids = {DM}
        table.fight.secrets, table.fight.round = [], 1
        table.fight.present, table.fight.names = {11, DM}, {"hero11": 11}
        self.dm_says(table, "Hero11, you're up")
        self.assertEqual(table.fight.style.order, "side")
        self.assertTrue(self.dm_says(table, "players go first"))  # restating it refreshes the line
        self.assertEqual(table.fight.style.dm_phrase(), "Initiative, as heard: by side")
        self.dm_says(table, "side initiative")
        self.assertEqual(table.fight.style.set_by["order"], "dm")

    async def test_it_is_written_only_when_it_changes(self) -> None:
        table, _bot = self.primed([11])
        keeper = self.keeper(table)
        self.dm_says(table, "players go first")
        await self.remember(keeper, table)
        await self.remember(keeper, table)  # nothing new
        self.dm_says(table, "the party acts")  # the same style again
        await self.remember(keeper, table)
        self.assertEqual(keeper._saved_calls.await_count, 1)
        self.dm_says(table, "everyone say what you're doing")
        await self.remember(keeper, table)
        self.assertEqual(keeper._saved_calls.await_args.args[2], "side+declare")

    async def test_a_failed_save_is_logged_and_tried_again_next_time(self) -> None:
        table, _bot = self.primed([11])
        keeper = self.keeper(table)
        keeper._saved_calls.side_effect = RuntimeError("down")
        self.dm_says(table, "players go first")
        with self.assertLogs("dmbot.bot", level="WARNING"):
            await self.remember(keeper, table)
        self.assertIsNone(table.fight.saved)
        keeper._saved_calls.side_effect = None
        await self.remember(keeper, table)
        self.assertEqual(table.fight.saved, "side")

    async def test_a_new_session_starts_from_the_kept_style_and_a_dm_line_overrides(self) -> None:
        table = self.table()
        table.fight = fightbot.FightState.starting_from("side")
        table.dm_user_ids = {DM}
        table.fight.secrets = []
        table.fight.round = 1
        table.fight.present = {11, DM}
        table.fight.names = {"hero11": 11}
        self.assertTrue(table.fight.style.known)
        self.assertEqual(table.fight.style.order, "side")
        self.dm_says(table, "we roll initiative every round")  # the DM states another style
        self.assertEqual(table.fight.style.order, "reroll")
        keeper = self.keeper(table)
        await self.remember(keeper, table)
        self.assertEqual(keeper._saved_calls.await_args.args[2], "reroll")


class Stored(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", 7)
        self.a2 = await self.campaigns.create(GUILD_A, "Alpha two", 7)
        self.b = await self.campaigns.create(GUILD_B, "Beta", 7)

    async def style(self, guild: int, campaign: str) -> str | None:
        found = await self.campaigns.get(guild, campaign)
        assert found is not None
        return found.initiative_style

    async def test_it_is_kept_per_campaign_and_server(self) -> None:
        self.assertIsNone(await self.style(GUILD_A, self.a.id))
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "side+declare")
        self.assertEqual(await self.style(GUILD_A, self.a.id), "side+declare")
        self.assertIsNone(await self.style(GUILD_A, self.a2.id))
        self.assertIsNone(await self.style(GUILD_B, self.b.id))
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, None)
        self.assertIsNone(await self.style(GUILD_A, self.a.id))

    async def test_only_the_listed_styles_can_be_kept(self) -> None:
        import psycopg

        with self.assertRaises(psycopg.Error):
            await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "whatever")

    async def test_it_goes_in_a_backup_and_an_old_backup_restores_as_none(self) -> None:
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "reroll")
        data = await self.campaigns.export(GUILD_A, self.a.id)
        self.assertEqual(data["campaign"]["initiative_style"], "reroll")
        restored = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertEqual(restored.initiative_style, "reroll")
        del data["campaign"]["initiative_style"]  # made before the style was kept
        older = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertIsNone(older.initiative_style)

    async def test_an_old_backup_with_the_old_switch_on_restores_as_side(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        del data["campaign"]["initiative_style"]  # made before the style was kept
        data["campaign"]["side_initiative"] = True
        seeded = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertEqual(seeded.initiative_style, "side")
        data["campaign"]["side_initiative"] = False
        plain = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertIsNone(plain.initiative_style)

    async def test_a_backup_that_keeps_a_style_keeps_it_whatever_the_old_switch_says(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        data["campaign"]["side_initiative"] = True
        data["campaign"]["initiative_style"] = "each"
        kept = await self.campaigns.import_backup(GUILD_B, data, 7)
        self.assertEqual(kept.initiative_style, "each")

    async def test_the_old_switch_is_still_written_to_a_backup_beside_the_style(self) -> None:
        await self.campaigns.set_side_initiative(GUILD_A, self.a.id, True)
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "side")
        data = await self.campaigns.export(GUILD_A, self.a.id)
        self.assertIs(data["campaign"]["side_initiative"], True)
        self.assertEqual(data["campaign"]["initiative_style"], "side")

    async def test_a_replaced_campaign_takes_the_backups_style(self) -> None:
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "each")
        data = await self.campaigns.export(GUILD_A, self.a.id)
        await self.campaigns.set_initiative_style(GUILD_A, self.a.id, "side")
        await self.campaigns.import_backup(GUILD_A, data, 7, replace_campaign_id=self.a.id)
        self.assertEqual(await self.style(GUILD_A, self.a.id), "each")

    async def test_a_damaged_style_in_a_backup_is_refused(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        damaged_values: list[Any] = ["sideways", 5, ["each"], {}, True]
        for damaged in damaged_values:
            data["campaign"]["initiative_style"] = damaged
            with self.assertRaises(CampaignError, msg=repr(damaged)):
                await self.campaigns.import_backup(GUILD_B, data, 7)


class Backfill(DatabaseTest):
    """Migrations run with no server set; 0130 must reach every row, and only the right ones."""

    async def test_the_old_switch_seeds_the_style_and_nothing_else_changes(self) -> None:
        from dmbot.db import Database, drop_schema
        from dmbot.schema import MIGRATIONS
        from tests.pg import TEST_URL

        before = [m for m in MIGRATIONS if m[0] < "0130"]
        await self.db.close()  # start again from a schema as it was before 0130
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        store = CampaignStore(self.db)
        on = await store.create(GUILD_A, "Switch on", 7)
        kept = await store.create(GUILD_A, "Switch on, style kept", 7)
        off = await store.create(GUILD_A, "Switch off", 7)
        far = await store.create(GUILD_B, "Other server", 7)
        await store.set_side_initiative(GUILD_A, on.id, True)
        await store.set_side_initiative(GUILD_A, kept.id, True)
        await store.set_initiative_style(GUILD_A, kept.id, "each+declare")
        await store.set_side_initiative(GUILD_B, far.id, True)
        await self.db.migrate()
        self.assertEqual(await self.style(store, GUILD_A, on.id), "side")
        self.assertEqual(await self.style(store, GUILD_A, kept.id), "each+declare")  # left alone
        self.assertIsNone(await self.style(store, GUILD_A, off.id))
        self.assertEqual(await self.style(store, GUILD_B, far.id), "side")  # every server
        async with self.db.unscoped() as conn:  # the opening is gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND tablename = 'campaigns'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        again = await store.get(GUILD_A, on.id)
        assert again is not None
        self.assertTrue(again.side_initiative)  # the column stays

    async def style(self, store: CampaignStore, guild: int, campaign: str) -> str | None:
        found = await store.get(guild, campaign)
        assert found is not None
        return found.initiative_style


if __name__ == "__main__":
    unittest.main()
