"""TimeBot part 2 (#998): durations from the rules index (the whole index), timers started from
the clock message's form and from a rules card, endings as the clock moves (a rest passing
several), who may press the buttons, the 20-timer limit, the backup round trip and isolation.
The store tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.dm_screen import clock as ui
from dmbot.dm_screen import effects as fx
from dmbot.dm_screen import rules_cards
from dmbot.rules.index import srd
from dmbot.timebot import clock as game
from dmbot.timebot import durations
from dmbot.timebot.effects import MAX_RUNNING, EffectsSection, EffectStore
from dmbot.timebot.store import ClockSection, ClockStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, PLAYER = 7, 8
NOON = game.from_day_and_time(4, 12)
UNTIMED_WORDS = ("instant", "until", "special")


class Durations(unittest.TestCase):
    def test_the_texts_in_the_rules(self) -> None:
        cases = {
            "1 minute": (1, False),
            "1 hour": (60, False),
            "8 hours": (480, False),
            "24 hours": (1440, False),
            "10 days": (14400, False),
            "Concentration, up to 1 minute": (1, True),
            "Concentration up to 10 minutes": (10, True),
            "Concentration, up to 1 hour.": (60, True),
            "Concentration, up to one minute": (1, True),
            "Up to 8 hours": (480, False),
            "1 round": (1, False),  # a round is 6 seconds: rounded up to a whole minute
            "Concentration, up to 6 rounds": (1, True),
        }
        for text, (minutes, mind) in cases.items():
            got = durations.parse(text)
            self.assertEqual((got.minutes, got.concentration), (minutes, mind), text)

    def test_what_is_not_a_length_is_not_timed(self) -> None:
        for text in (
            "Instantaneous",
            "Until dispelled",
            "Until dispelled or triggered",
            "Special",
            "",
            None,
        ):
            self.assertFalse(durations.parse(text).timed, text)

    def test_what_the_dm_types(self) -> None:
        self.assertEqual(durations.parse("10 minutes").minutes, 10)
        self.assertEqual(durations.parse("8 hr").minutes, 480)
        self.assertEqual(durations.parse("30 min").minutes, 30)
        self.assertEqual(durations.parse("2 days").minutes, 2880)
        self.assertFalse(durations.parse("0 minutes").timed)
        self.assertFalse(durations.parse("soon").timed)

    def test_every_spell_in_the_index_parses_or_is_plainly_untimed(self) -> None:
        unread = []
        for entry in srd().entries:
            if entry.kind != "spell":
                continue
            text = str(entry.details.get("duration", ""))
            if not durations.parse(text).timed and not any(
                w in text.lower() for w in UNTIMED_WORDS
            ):
                unread.append((entry.name, text))
        self.assertEqual(unread, [], "durations that do not parse")

    def test_words(self) -> None:
        self.assertEqual(durations.words(1), "1 minute")
        self.assertEqual(durations.words(10), "10 minutes")
        self.assertEqual(durations.words(60), "1 hour")
        self.assertEqual(durations.words(480), "8 hours")
        self.assertEqual(durations.words(1440), "1 day")


class Cards(unittest.TestCase):
    def test_time_it_is_only_on_a_card_for_a_timed_spell(self) -> None:
        plain = rules_cards.card_view(1, "abcdef01")
        timed = rules_cards.card_view(1, "abcdef01", timed=True)
        self.assertEqual(len(timed.children), len(plain.children) + 1)
        labels = [cast(Any, i).item.label for i in timed.children]
        self.assertIn("Time it", labels)
        for item in timed.children:
            custom_id = str(cast(Any, item).item.custom_id)
            template = cast(Any, type(item)).__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(custom_id))
            self.assertLessEqual(len(custom_id), 100)


class Fake:
    def __init__(self, clocks: ClockStore, effects: EffectStore, campaigns: CampaignStore) -> None:
        self.clocks, self.effects = clocks, effects
        self.campaigns = SimpleNamespace(
            optional_rule_overrides=AsyncMock(return_value={}), get=campaigns.get
        )
        self.posted: list[tuple[int, str, Any]] = []

    async def post_message(self, channel_id: int, text: str, view: Any = None) -> None:
        self.posted.append((channel_id, text, view))

    def get_channel(self, channel_id: int) -> None:
        return None


class Timers(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.campaigns.register_section(EffectsSection())
        self.clocks, self.effects = ClockStore(self.db), EffectStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", DM)
        self.b = await self.campaigns.create(GUILD_B, "Beta", DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = 555 WHERE id = %s", (self.a.id,)
            )
        self.client = Fake(self.clocks, self.effects, self.campaigns)

    async def set_clock(self, minute: int = NOON) -> None:
        await ui.press(self.client, GUILD_A, self.a.id, DM, "set", set_to=minute)

    def interaction(self, user: int = DM) -> Any:
        followup = AsyncMock()
        return SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=user),
            client=self.client,
            message=None,
            response=SimpleNamespace(
                defer=AsyncMock(), is_done=lambda: True, send_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=followup),
            edit_original_response=AsyncMock(),
        )

    async def test_a_timer_from_the_form_uses_the_spells_own_length(self) -> None:
        await self.set_clock()
        it = self.interaction()
        await fx.start_timer(it, self.a.id, "Bless", "Mira", "")
        [effect] = await self.effects.running(GUILD_A, self.a.id)
        self.assertEqual((effect.name, effect.target, effect.minutes), ("Bless", "Mira", 1))
        self.assertTrue(effect.concentration)
        self.assertEqual(effect.ends, NOON + 1)

    async def test_the_dm_can_type_a_length_and_a_name_the_index_lacks(self) -> None:
        await self.set_clock()
        await fx.start_timer(self.interaction(), self.a.id, "Torch", "", "1 hour")
        [effect] = await self.effects.running(GUILD_A, self.a.id)
        self.assertEqual((effect.minutes, effect.target, effect.concentration), (60, None, False))

    async def test_no_length_known_asks_for_one_and_starts_nothing(self) -> None:
        await self.set_clock()
        it = self.interaction()
        await fx.start_timer(it, self.a.id, "Torch", "", "")
        await fx.start_timer(it, self.a.id, "Fireball", "", "")  # instantaneous
        self.assertEqual(await self.effects.running(GUILD_A, self.a.id), [])

    async def test_without_a_clock_nothing_starts(self) -> None:
        it = self.interaction()
        await fx.start_timer(it, self.a.id, "Bless", "", "")
        self.assertEqual(await self.effects.running(GUILD_A, self.a.id), [])
        self.assertIn("Set the game clock", it.followup.send.await_args.args[0])

    async def test_only_dms_start_or_end_timers(self) -> None:
        await self.set_clock()
        with self.assertRaises(CampaignError):
            await self.effects.start(GUILD_A, self.a.id, PLAYER, "Bless", None, 1, True)
        effect = await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 1, True)
        for method in (self.effects.end, self.effects.extend):
            with self.assertRaises(CampaignError):
                await method(GUILD_A, self.a.id, PLAYER, effect.number)
        self.assertEqual(len(await self.effects.running(GUILD_A, self.a.id)), 1)

    async def test_the_limit_is_twenty(self) -> None:
        await self.set_clock()
        for n in range(MAX_RUNNING):
            await self.effects.start(GUILD_A, self.a.id, DM, f"T{n}", None, 60, False)
        with self.assertRaises(CampaignError):
            await self.effects.start(GUILD_A, self.a.id, DM, "One more", None, 60, False)

    async def test_a_rest_passing_several_says_each_once(self) -> None:
        await self.set_clock()
        for name, minutes in (("Bless", 1), ("Mage Armor", 480), ("Torch", 30), ("Long one", 5000)):
            await self.effects.start(GUILD_A, self.a.id, DM, name, "Mira", minutes, False)
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        await ui.press(self.client, GUILD_A, self.a.id, DM, "short")  # +1 hour: Bless, Torch
        await fx.announce_due(self.client, campaign, NOON + 60)
        said = [text for _, text, _ in self.client.posted]
        self.assertEqual(len(said), 2)
        self.assertIn("Bless on Mira has likely ended", said[0])
        self.assertIn("Torch on Mira has likely ended", said[1])
        again = len(self.client.posted)
        await fx.announce_due(self.client, campaign, NOON + 61)  # nothing is said twice
        self.assertEqual(len(self.client.posted), again)
        await fx.announce_due(self.client, campaign, NOON + 480)  # Mage Armor now
        self.assertEqual(len(self.client.posted), again + 1)

    async def test_ended_and_still_going(self) -> None:
        await self.set_clock()
        effect = await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 1, True)
        await ui.press(self.client, GUILD_A, self.a.id, DM, "h1")
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        await fx.announce_due(self.client, campaign, NOON + 60)
        more = await self.effects.extend(GUILD_A, self.a.id, DM, effect.number)
        self.assertEqual(more.ends, NOON + 60 + 10)
        self.assertFalse(more.told)  # it will be said again when that passes
        await fx.announce_due(self.client, campaign, NOON + 71)
        self.assertEqual(len(self.client.posted), 2)
        await self.effects.end(GUILD_A, self.a.id, DM, effect.number)
        self.assertEqual(await self.effects.running(GUILD_A, self.a.id), [])
        with self.assertRaises(CampaignError):
            await self.effects.end(GUILD_A, self.a.id, DM, effect.number)

    async def test_the_clock_message_lists_the_soonest_five(self) -> None:
        await self.set_clock()
        for n in range(7):
            await self.effects.start(GUILD_A, self.a.id, DM, f"T{n}", None, 10 * (7 - n), False)
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        text = await ui.render(self.client, campaign, stored.clock)
        lines = text.splitlines()
        self.assertEqual(len(lines), 1 + 5 + 1)
        self.assertIn("T6", lines[1])  # the one that ends first
        self.assertIn("and 2 more", lines[-1])

    async def test_the_buttons_fit_discord(self) -> None:
        for action in ("end", "more"):
            button = fx.EffectButton("0123456789abcdef0123456789abcdef", 123456, action)
            custom_id = str(button.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(type(button).__discord_ui_compiled_template__.fullmatch(custom_id))

    async def test_a_press_by_a_player_changes_nothing(self) -> None:
        await self.set_clock()
        effect = await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 1, True)
        it = self.interaction(PLAYER)
        await fx.EffectButton(self.a.id, effect.number, "end").callback(it)
        self.assertEqual(len(await self.effects.running(GUILD_A, self.a.id)), 1)

    async def test_timers_go_in_a_backup_and_come_back(self) -> None:
        await self.set_clock()
        await self.effects.start(GUILD_A, self.a.id, DM, "Bless", "Mira", 1, True)
        await self.effects.start(GUILD_A, self.a.id, DM, "Torch", None, 60, False)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        got = await self.effects.running(GUILD_B, restored.id)
        self.assertEqual(
            [(e.name, e.target, e.minutes) for e in got],
            [("Bless", "Mira", 1), ("Torch", None, 60)],
        )
        stored = await self.clocks.get(GUILD_B, restored.id)
        assert stored is not None and stored.clock.minute == NOON

    async def test_a_damaged_timer_in_a_backup_is_refused(self) -> None:
        section = EffectsSection()
        good = {
            "name": "Bless",
            "target": None,
            "minutes": 1,
            "concentration": True,
            "ends": 5,
            "told": False,
        }
        section.check([good])
        for bad in (
            {**good, "minutes": 0},
            {**good, "name": ""},
            {**good, "ends": -1},
            {**good, "told": "no"},
            {"name": "x"},
        ):
            with self.assertRaises(CampaignError):
                section.check([bad])
        with self.assertRaises(CampaignError):
            section.check([good] * (MAX_RUNNING + 1))

    async def test_campaigns_and_servers_never_share_timers(self) -> None:
        await self.set_clock()
        await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 1, True)
        self.assertEqual(await self.effects.running(GUILD_B, self.b.id), [])
        self.assertEqual(await self.effects.running(GUILD_B, self.a.id), [])  # another server
        self.assertEqual(await self.effects.claim_due(GUILD_B, self.a.id, NOON + 1000), [])

    async def test_deleting_the_campaign_deletes_its_timers(self) -> None:
        await self.set_clock()
        await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 1, True)
        await self.campaigns.delete(GUILD_A, self.a.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM game_effects")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
