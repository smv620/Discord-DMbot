"""MapBot's live note to the DM (#4670, MapBot slice 4c-2; design §9; Supervisor's ruling): the
campaign's "Map checks" switch (on by default, kept per campaign and in backups), what a DM's line
states against the kept maps, the note's words and its one button, and the Settings button shown
only while the map answers are on. Invented names. The store classes need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import CampaignStore, decode_backup, encode_backup
from dmbot.dm_screen.settings import MapChecksButton, settings_view
from dmbot.maps import disagree, said_phrases
from dmbot.maps import measure_words as words
from dmbot.maps.store import SaidOff
from dmbot.ui import map_checks
from tests.pg import DatabaseTest
from tests.test_map_measure import a_map
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

DM, OTHER = 7, 9
GUILD = 1


class TheSwitch(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.c = await self.store.create(GUILD, "Frozen Wastes", DM)

    async def test_on_by_default_and_kept_per_campaign(self) -> None:
        self.assertTrue(self.c.map_checks)
        other = await self.store.create(GUILD, "Other", DM)
        off = await self.store.set_map_checks(GUILD, self.c.id, False)
        self.assertFalse(off.map_checks)
        again = await self.store.get(GUILD, other.id)
        assert again is not None
        self.assertTrue(again.map_checks)

    async def test_the_settings_button_only_while_the_map_answers_are_on(self) -> None:
        def has(view: Any) -> bool:
            return any(isinstance(i, MapChecksButton) for i in view.children)

        self.assertTrue(has(settings_view(self.c, None, DM, map_checks=True)))
        self.assertFalse(has(settings_view(self.c, None, DM)))
        self.assertFalse(has(settings_view(self.c, None, OTHER, map_checks=True)))  # (DMs only)
        from dmbot.dm_screen.settings import settings_text

        self.assertIn("**Map checks:** on", settings_text(self.c, None, DM, map_checks=True))
        self.assertNotIn("Map checks", settings_text(self.c, None, DM))

    async def test_a_backup_keeps_it_and_an_older_one_has_it_on(self) -> None:
        await self.store.set_map_checks(GUILD, self.c.id, False)
        backup = await self.store.export(GUILD, self.c.id)
        copy = await self.store.import_backup(2, decode_backup(encode_backup(backup)), DM)
        self.assertFalse(copy.map_checks)
        backup["campaign"].pop("map_checks")
        older = await self.store.import_backup(3, decode_backup(encode_backup(backup)), DM)
        self.assertTrue(older.map_checks)
        backup["campaign"]["map_checks"] = "no"
        with self.assertRaises(CampaignError):
            await self.store.import_backup(4, decode_backup(encode_backup(backup)), DM)


class WhatTheDMSaid(PinsBase):  # (Postgres: Targos and Bryn Shander 60 miles apart, east-west)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        await self.record()

    async def said(self, line: str) -> list[SaidOff]:
        return await self.maps.said_off(GUILD_A, self.one.id, line)

    async def test_a_distance_far_shorter_than_the_map(self) -> None:
        (found,) = await self.said("Targos is 20 miles from Bryn Shander.")
        self.assertEqual((found.a_name, found.b_name), ("Targos", "Bryn Shander"))
        assert isinstance(found.off, disagree.Distance)
        self.assertEqual(disagree.amount(found.off), "60 miles")
        self.assertEqual(await self.said("Targos is 55 miles from Bryn Shander."), [])
        self.assertEqual(await self.said("Targos is 90 miles from Bryn Shander."), [])

    async def test_a_direction_only_when_north_was_read_or_set(self) -> None:
        # (Targos is west of Bryn Shander on the map; north is assumed until the DM sets it)
        self.assertEqual(await self.said("Targos is north of Bryn Shander."), [])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaign_maps SET north_from = 'dm'")
        (found,) = await self.said("Targos is north of Bryn Shander.")
        assert isinstance(found.off, disagree.Direction)
        self.assertEqual(found.off.map_way, "west")
        self.assertEqual(await self.said("Targos is west of Bryn Shander."), [])

    async def test_talk_that_states_nothing_reads_nothing(self) -> None:
        self.assertEqual(await self.said("We ride for Bryn Shander at dawn."), [])
        self.assertEqual(await self.said("Luskan is 2 miles from Targos."), [])

    async def test_a_map_the_dm_said_no_to_is_never_used(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaign_maps SET use_ok = FALSE")
        self.assertEqual(await self.said("Targos is 20 miles from Bryn Shander."), [])


def found(kind: str = said_phrases.FAR) -> SaidOff:
    if kind == said_phrases.FAR:
        said = said_phrases.Said(kind, "t", "b", 20, "20 miles")
        return SaidOff(
            said, "Targos", "Bryn Shander", disagree.Distance(a_map(title="Ten-Towns"), 20, 35)
        )
    said = said_phrases.Said(kind, "b", "t", 0.0, "north")
    return SaidOff(
        said, "Bryn Shander", "Targos", disagree.Direction(a_map(title="Ten-Towns"), "east")
    )


class TheNote(unittest.IsolatedAsyncioTestCase):
    def test_the_words(self) -> None:
        # (the ux-critic's: what was said, then what the map shows; never "wrong", no link)
        self.assertEqual(
            map_checks.note_text(found()),
            "You said 20 miles from Targos to Bryn Shander. On the Ten-Towns map it's about"
            " 35 miles in a straight line.",
        )
        self.assertEqual(
            map_checks.note_text(found(said_phrases.WAY)),
            "You said Bryn Shander is north of Targos. On the Ten-Towns map, Bryn Shander is about"
            " east of Targos.",
        )

    async def test_one_button_and_no_link(self) -> None:
        view = map_checks.note_view(GUILD, "c" * 32)
        labels = [getattr(getattr(i, "item", i), "label", None) for i in view.children]
        self.assertEqual(labels, [words.STOP])
        self.assertTrue(all(getattr(i, "url", None) is None for i in view.children))

    def press(self, user: int, campaign: Any) -> tuple[Any, Any]:
        store = SimpleNamespace(get=AsyncMock(return_value=campaign), set_map_checks=AsyncMock())
        sent = AsyncMock()
        interaction = SimpleNamespace(
            client=SimpleNamespace(campaigns=store),
            user=SimpleNamespace(id=user),
            response=SimpleNamespace(send_message=sent),
        )
        return interaction, (store, sent)

    async def test_stop_turns_it_off_for_a_dm_only(self) -> None:
        camp = SimpleNamespace(dm_user_ids=frozenset({DM}))
        button = map_checks.StopMapChecksButton(GUILD, "c" * 32)
        interaction, (store, sent) = self.press(DM, camp)
        await button.callback(interaction)
        store.set_map_checks.assert_awaited_once_with(GUILD, "c" * 32, False)
        self.assertEqual(sent.await_args.args[0], words.STOPPED)
        interaction, (store, sent) = self.press(OTHER, camp)
        await button.callback(interaction)
        store.set_map_checks.assert_not_awaited()
        self.assertEqual(sent.await_args.args[0], map_checks.ONLY_DM)


class TheBot(unittest.IsolatedAsyncioTestCase):  # (`DMBot._map_check` on a stand-in for the bot)
    def make(self, *, checks: bool = True, plan: bool = True) -> tuple[Any, Any]:
        from dmbot.memory.live_heads_up import LiveSession

        table = SimpleNamespace(
            guild_id=GUILD, campaign_id="c" * 32, live_heads_up=LiveSession(), label_waits={},
            speakers=SimpleNamespace(window_s=0.05),
        )  # fmt: skip
        camp = SimpleNamespace(dm_user_ids=frozenset({DM}), map_checks=checks)
        bot = SimpleNamespace(
            map_store=SimpleNamespace(said_off=AsyncMock(return_value=[found()])),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
            consent=SimpleNamespace(has_consent=lambda g, u: u in {DM, OTHER}),
            plan_allows=AsyncMock(return_value=plan),
            tables={GUILD: table},
            _dm_message=AsyncMock(return_value=object()),
        )
        from dmbot.bot import DMBot

        bot._label_waiter = DMBot._label_waiter
        return bot, table

    async def check(self, bot: Any, table: Any, speaker: int = DM) -> None:
        from dmbot.bot import DMBot

        await DMBot._map_check(bot, table, speaker, "Targos is 20 miles from Bryn Shander.")

    async def test_the_dm_gets_one_note_for_a_pair_in_a_session(self) -> None:
        bot, table = self.make()
        await self.check(bot, table)
        bot._dm_message.assert_awaited_once()
        user, text, view = bot._dm_message.await_args.args
        self.assertEqual(user, DM)
        self.assertTrue(text.startswith("You said 20 miles from Targos"), text)
        self.assertIsNotNone(view)
        await self.check(bot, table)  # (said again: quiet for the rest of the session)
        self.assertEqual(bot._dm_message.await_count, 1)

    async def test_never_for_a_player_or_with_map_checks_off_or_no_plan(self) -> None:
        for bot, table, speaker in (
            (*self.make(), OTHER),
            (*self.make(checks=False), DM),
            (*self.make(plan=False), DM),
        ):
            await self.check(bot, table, speaker)
            bot._dm_message.assert_not_awaited()

    async def test_never_without_consent_or_after_the_session(self) -> None:
        bot, table = self.make()
        bot.consent = SimpleNamespace(has_consent=lambda g, u: False)
        await self.check(bot, table)
        bot2, table2 = self.make()
        bot2.tables = {}
        await self.check(bot2, table2)
        bot._dm_message.assert_not_awaited()
        bot2._dm_message.assert_not_awaited()

    async def test_consent_withdrawn_while_the_maps_are_read_sends_nothing(self) -> None:
        bot, table = self.make()
        agreed = {DM}
        bot.consent = SimpleNamespace(has_consent=lambda g, u: u in agreed)

        async def said_off(*args: Any) -> list[SaidOff]:
            agreed.clear()  # (they stopped being recorded during the read)
            return [found()]

        bot.map_store.said_off = said_off
        await self.check(bot, table)
        bot._dm_message.assert_not_awaited()


class TheSpeakerLabel(TheBot):  # (#4690: a line said as a named NPC is no note)
    async def labelled(self, entity: str | None) -> Any:
        import asyncio

        from dmbot import bot as bot_module
        from dmbot.bot import DMBot

        bot, table = self.make()
        check = asyncio.ensure_future(
            DMBot._map_check(bot, table, DM, "Targos is 20 miles from Bryn Shander.", 5)
        )
        await asyncio.sleep(0)
        if entity is not None:
            DMBot._label_known(table, DM, 5, entity)
            await check
        else:  # (the label never comes: the wait ends, and it is the DM's own words)
            old = bot_module.TOPIC_CALL_TIMEOUT_S
            bot_module.TOPIC_CALL_TIMEOUT_S = -1
            try:
                await check
            finally:
                bot_module.TOPIC_CALL_TIMEOUT_S = old
            # (left for the label: every line of a window is answered, "" at worst, and that
            # answer clears it; a look-up may share the same wait)
            DMBot._label_known(table, DM, 5, "")
        self.assertEqual(table.label_waits, {})  # (nothing left waiting)
        return bot

    async def test_a_line_said_as_an_npc_gets_no_note(self) -> None:
        bot = await self.labelled("Mira the innkeeper")
        bot._dm_message.assert_not_awaited()

    async def test_narration_or_the_dm_gets_the_note(self) -> None:
        from dmbot.sidebar.ask import NARRATING

        for entity in ("", NARRATING):
            bot = await self.labelled(entity)
            bot._dm_message.assert_awaited_once()

    async def test_no_label_in_time_gets_the_note(self) -> None:
        bot = await self.labelled(None)
        bot._dm_message.assert_awaited_once()
