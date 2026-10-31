"""Split-party clocks (#1607): the pure rules (names, drift, which time Rejoin keeps), then the
store (the buttons act on the picked group, timers count on their own group, Rejoin and the
two Undos, backups, isolation), what the DM sees, and that spoken phrases move nothing while
split. The store tests need Postgres."""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import clock as ui
from dmbot.schema import MIGRATIONS
from dmbot.timebot import clock as game
from dmbot.timebot import combat_phrases, split
from dmbot.timebot.clock import Clock
from dmbot.timebot.effects import EffectsSection, EffectStore
from dmbot.timebot.store import ClockSection, ClockStore, Restored
from tests import test_timebot as base
from tests.pg import TEST_URL, DatabaseTest

DM, PLAYER, OTHER_DM = base.DM, base.PLAYER, base.OTHER_DM
GUILD_A, GUILD_B = base.GUILD_A, base.GUILD_B
NOON4 = base.NOON4
HOUR = 60


class Rules(unittest.TestCase):
    def test_names_are_cleaned_and_at_least_two_are_needed(self) -> None:
        self.assertEqual(split.check_names(["Tower", " Camp ", ""]), ["Tower", "Camp"])
        self.assertEqual(split.check_names(["", "Tower", "Camp"]), ["Tower", "Camp"])
        self.assertEqual(split.check_names(["Tower", "", ""]), split.NEED_TWO)
        self.assertEqual(split.check_names(["", "", ""]), split.NEED_TWO)

    def test_two_groups_cannot_share_a_name_ignoring_capitals(self) -> None:
        self.assertEqual(split.check_names(["Tower", "tower"]), split.SAME_NAME)

    def test_a_name_is_short_one_line_and_pings_nobody(self) -> None:
        [first, second] = cast(
            list[str], split.check_names(["<@123> the\nTower of Doom, long name", "Camp"])
        )
        self.assertLessEqual(len(first), split.NAME_MAX)
        self.assertNotIn("@", first)
        self.assertNotIn("<", first)
        self.assertNotIn("\n", first)
        self.assertEqual(second, "Camp")

    def test_every_group_starts_at_the_time_and_last_long_rest_the_party_had(self) -> None:
        groups = split.start(Clock(NOON4, NOON4 - 600, 5), ["Tower", "Camp", "Hall"])
        self.assertEqual([g.position for g in groups], [1, 2, 3])
        self.assertEqual({g.clock.minute for g in groups}, {NOON4})
        self.assertEqual({g.clock.last_long_rest for g in groups}, {NOON4 - 600})

    def test_the_drift_line_waits_for_more_than_eight_hours(self) -> None:
        a, b = (split.Group(i, n, Clock(NOON4, 0)) for i, n in ((1, "Tower"), (2, "Camp")))
        self.assertIsNone(split.drift((a, b)))
        exactly = split.Group(1, "Tower", Clock(NOON4 + 8 * HOUR, 0))
        self.assertIsNone(split.drift((exactly, b)))
        over = split.Group(1, "Tower", Clock(NOON4 + 8 * HOUR + 1, 0))
        found = split.drift((over, b))
        assert found is not None
        self.assertEqual(split.drift_line(found), "Tower is 8 hours ahead of Camp.")

    def test_the_drift_line_names_the_furthest_ahead_and_the_furthest_behind(self) -> None:
        groups = (
            split.Group(1, "Tower", Clock(NOON4 + 2 * HOUR, 0)),
            split.Group(2, "Camp", Clock(NOON4, 0)),
            split.Group(3, "Hall", Clock(NOON4 + 9 * HOUR + 30, 0)),
        )
        found = split.drift(groups)
        assert found is not None
        self.assertEqual(split.drift_line(found), "Hall is 9 hours ahead of Camp.")

    def test_rejoin_offers_the_latest_time_first(self) -> None:
        groups = (
            split.Group(1, "Tower", Clock(NOON4, 0)),
            split.Group(2, "Camp", Clock(NOON4 + 90, 0)),
            split.Group(3, "Hall", Clock(NOON4, 0)),
        )
        self.assertEqual([g.name for g in split.latest_first(groups)], ["Camp", "Tower", "Hall"])

    def test_rejoin_keeps_the_chosen_time_and_the_latest_rest(self) -> None:
        groups = (
            split.Group(1, "Tower", Clock(NOON4 + 5 * HOUR, NOON4 - 100, 7)),
            split.Group(2, "Camp", Clock(NOON4 + 2 * HOUR, NOON4 + 60)),
        )
        joined = split.rejoin(groups, 1)
        self.assertEqual(joined.minute, NOON4 + 5 * HOUR)
        self.assertEqual(joined.last_long_rest, NOON4 + 60)  # Camp rested more recently
        self.assertIsNone(joined.tired_told_for)  # said for a rest that is no longer the last
        # Keeping the earlier time: a rest can't be in the future, so it comes back to that time.
        early = split.rejoin(
            (
                split.Group(1, "Tower", Clock(NOON4, 0)),
                split.Group(2, "Camp", Clock(NOON4 + 10 * HOUR, NOON4 + 9 * HOUR)),
            ),
            1,
        )
        self.assertEqual((early.minute, early.last_long_rest), (NOON4, NOON4))

    def test_rejoin_keeps_a_24_hour_line_already_said_for_the_same_rest(self) -> None:
        groups = (
            split.Group(1, "Tower", Clock(NOON4 + HOUR, 100, 100)),
            split.Group(2, "Camp", Clock(NOON4, 100)),
        )
        self.assertEqual(split.rejoin(groups, 1).tired_told_for, 100)

    def test_a_timer_keeps_its_time_left_when_its_group_takes_the_kept_time(self) -> None:
        self.assertEqual(split.shifted(1000, 900, 1500), 1600)
        self.assertEqual(split.shifted(50, 900, 100), 0)  # (never before the start)

    def test_the_dm_sees_exact_times_and_the_players_only_a_rough_time(self) -> None:
        groups = (
            split.Group(1, "Tower", Clock(game.from_day_and_time(4, 14, 30), 0)),
            split.Group(2, "Camp", Clock(game.from_day_and_time(4, 16, 10), 0)),
        )
        self.assertEqual(split.dm_line(groups), "Tower: Day 4, 14:30 · Camp: Day 4, 16:10")
        for line in split.players_lines(groups):
            self.assertIsNone(re.search(r"\d:\d\d", line), line)
        self.assertEqual(split.players_lines(groups)[0], "Tower: Day 4, afternoon")


class Wiring(unittest.TestCase):
    CID = "0123456789abcdef0123456789abcdef"

    def test_every_new_id_fits_discord_and_matches_its_own_template(self) -> None:
        group = split.Group(2, "Camp", Clock(0, 0))
        big = Clock(game.MAX_MINUTE, game.MAX_MINUTE, None, 99, 59)
        items: list[tuple[Any, Any]] = [
            (ui.ClockButton(self.CID, "split").item.custom_id, ui.ClockButton),
            (ui.ClockButton(self.CID, "rejoin").item.custom_id, ui.ClockButton),
            (ui.ClockPicker(self.CID, (group,), 2).item.custom_id, ui.ClockPicker),
            (ui.SplitUndoButton(self.CID, "split", "0a1b2c3d").item.custom_id, ui.SplitUndoButton),
            (
                ui.SplitUndoButton(self.CID, "rejoin", "ffffffff").item.custom_id,
                ui.SplitUndoButton,
            ),
            (
                ui.ClockUndoButton(self.CID, ui.undo_id(big, big, group)).item.custom_id,
                ui.ClockUndoButton,
            ),
            (
                ui.CombatUndoButton(self.CID, ui.combat_undo_id(big, big, group)).item.custom_id,
                ui.CombatUndoButton,
            ),
        ]
        for custom_id, cls in items:
            assert custom_id is not None and len(custom_id) <= 100, custom_id
            self.assertIsNotNone(cls.__discord_ui_compiled_template__.fullmatch(custom_id))

    def test_an_undo_for_a_group_carries_the_group_and_one_without_still_works(self) -> None:
        group = split.Group(3, "Hall", Clock(0, 0))
        c = Clock(1000, 500)
        self.assertTrue(ui.undo_id(c, c, group).endswith(":g3"))
        self.assertEqual(ui.ClockUndoButton(self.CID, ui.undo_id(c, c, group)).group, 3)
        self.assertIsNone(ui.ClockUndoButton(self.CID, ui.undo_id(c, c)).group)
        self.assertEqual(ui.CombatUndoButton(self.CID, ui.combat_undo_id(c, c, group)).group, 3)
        self.assertIsNone(ui.CombatUndoButton(self.CID, ui.combat_undo_id(c, c)).group)

    def test_the_panel_offers_split_when_one_and_rejoin_when_split(self) -> None:
        def labels(is_split: bool) -> list[str]:
            children: list[Any] = list(ui.panel_view(self.CID, is_split).children)
            return [str(getattr(i, "item", i).label) for i in children]

        self.assertIn("Split the party", labels(False))
        self.assertNotIn("Rejoin the party", labels(False))
        self.assertIn("Rejoin the party", labels(True))
        self.assertNotIn("Split the party", labels(True))

    def test_the_panel_still_fits_five_buttons_a_row(self) -> None:
        rows: dict[int, int] = {}
        for action in (*ui.PANEL, "split"):
            row = ui.ACTIONS[action][3]
            rows[row] = rows.get(row, 0) + 1
        self.assertTrue(all(n <= 5 for n in rows.values()))


class Fake(base.Fake):
    """What the buttons need from the bot, with timers and a place to post."""

    def __init__(self, clocks: ClockStore, campaigns: Any, effects: EffectStore) -> None:
        super().__init__(clocks, campaigns, {})
        self.effects = effects
        self.posted: list[tuple[int, str, Any]] = []
        self.user = SimpleNamespace(id=1)
        self.channels: dict[int, Any] = {}

    def channel(self, channel_id: int) -> Any:
        """A channel with nothing said in it yet (the players' line looks at its recent history)."""

        async def history(*, limit: int = 100) -> Any:
            return
            yield

        found = MagicMock(spec=discord.TextChannel)
        found.id, found.history = channel_id, history
        self.channels[channel_id] = found
        return found

    async def post_message(self, channel_id: int, text: str, view: Any = None, **_: Any) -> Any:
        self.posted.append((channel_id, text, view))
        return SimpleNamespace(
            id=len(self.posted),
            channel=SimpleNamespace(id=channel_id),
            pin=AsyncMock(),
            jump_url="https://discord.example/x",
        )

    def get_channel(self, channel_id: int) -> Any:
        return self.channels.get(channel_id)  # (none unless a test made one)


class Party(DatabaseTest):
    """A campaign whose DMs are DM and OTHER_DM, with a clock set to noon on Day 4."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.campaigns.register_section(EffectsSection())
        self.clocks, self.effects = ClockStore(self.db), EffectStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", DM)
        self.b = await self.campaigns.create(GUILD_B, "Beta", DM)
        await self.campaigns.add_dm(GUILD_A, self.a.id, OTHER_DM)
        self.fake = Fake(self.clocks, self.campaigns, self.effects)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = 555 WHERE id = %s", (self.a.id,)
            )
        await self.press("set", set_to=NOON4)

    async def press(self, action: str, user: int = DM, *, set_to: int | None = None) -> ui.Result:
        return await ui.press(self.fake, GUILD_A, self.a.id, user, action, set_to=set_to)

    async def stored(self) -> Any:
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        return stored

    async def split_up(self, *names: str) -> str:
        _, token = await self.clocks.split(GUILD_A, self.a.id, DM, list(names or ("Tower", "Camp")))
        return token

    async def minutes(self) -> dict[str, int]:
        stored = await self.stored()
        return {g.name: g.clock.minute for g in stored.groups}

    def interaction(self, user: int = DM, values: list[str] | None = None) -> Any:
        return SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=user),
            client=self.fake,
            message=None,
            data={"values": values} if values is not None else {},
            response=SimpleNamespace(
                defer=AsyncMock(),
                is_done=lambda: True,
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )


class Splitting(Party):
    async def test_the_groups_start_at_the_current_time_and_the_first_is_picked(self) -> None:
        await self.split_up("Tower", "Camp", "Hall")
        stored = await self.stored()
        self.assertEqual([g.name for g in stored.groups], ["Tower", "Camp", "Hall"])
        self.assertEqual({g.clock.minute for g in stored.groups}, {NOON4})
        self.assertEqual({g.clock.last_long_rest for g in stored.groups}, {NOON4})
        self.assertEqual(stored.picked, 1)
        self.assertEqual(stored.clock, stored.groups[0].clock)

    async def test_only_a_dm_can_split_pick_or_rejoin(self) -> None:
        with self.assertRaises(CampaignError):
            await self.clocks.split(GUILD_A, self.a.id, PLAYER, ["Tower", "Camp"])
        await self.split_up()
        with self.assertRaises(CampaignError):
            await self.clocks.pick(GUILD_A, self.a.id, PLAYER, 2)
        with self.assertRaises(CampaignError):
            await self.clocks.rejoin(GUILD_A, self.a.id, PLAYER, 1)
        self.assertTrue((await self.stored()).split)

    async def test_it_needs_a_clock_and_cannot_be_split_twice_or_in_a_fight(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Gamma", DM)
        with self.assertRaises(CampaignError):  # no clock yet
            await self.clocks.split(GUILD_A, other.id, DM, ["Tower", "Camp"])
        await self.press("cstart")
        with self.assertRaisesRegex(CampaignError, "End combat first"):
            await self.split_up()
        await self.press("cend")
        await self.split_up()
        with self.assertRaisesRegex(CampaignError, "already split"):
            await self.split_up()

    async def test_the_buttons_act_on_the_picked_group_only(self) -> None:
        await self.split_up()
        await self.press("h1")
        self.assertEqual(await self.minutes(), {"Tower": NOON4 + 60, "Camp": NOON4})
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("long")
        stored = await self.stored()
        self.assertEqual(await self.minutes(), {"Tower": NOON4 + 60, "Camp": NOON4 + 480})
        self.assertEqual(stored.clock.minute, NOON4 + 480)
        self.assertEqual(stored.groups[1].clock.last_long_rest, NOON4 + 480)
        self.assertEqual(stored.groups[0].clock.last_long_rest, NOON4)  # Tower did not rest
        # The clock from before the split is left as it was, for Rejoin and Undo.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT minute FROM game_clocks")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["minute"], NOON4)

    async def test_the_pick_is_remembered_across_a_restart(self) -> None:
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        again = (await ClockStore(self.db).get(GUILD_A, self.a.id)) or None
        assert again is not None
        self.assertEqual(
            (again.picked, again.picked_group.name if again.picked_group else ""), (2, "Camp")
        )

    async def test_picking_a_group_that_is_not_there_is_refused(self) -> None:
        await self.split_up()
        with self.assertRaises(CampaignError):
            await self.clocks.pick(GUILD_A, self.a.id, DM, 3)
        self.assertEqual((await self.stored()).picked, 1)

    async def test_dawn_noon_dusk_and_the_24_hour_line_are_said_per_group(self) -> None:
        await self.split_up()
        crossed = await self.press("long")  # Tower: 12:00 -> 20:00 (a long rest is quiet)
        self.assertEqual(crossed.lines, [])
        result = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=24 * HOUR)
        self.assertTrue(result.lines)
        said = [line for line in result.lines if "ahead of" not in line]  # (the drift line is not)
        self.assertTrue(all(line.startswith("Tower: ") for line in said), result.lines)
        self.assertTrue(any("24 hours since the last long rest" in line for line in result.lines))
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        camp = await self.press("h1")  # Camp: 12:00 -> 13:00, nothing passes
        self.assertEqual(camp.lines, [])

    async def test_a_clock_that_is_not_split_says_its_lines_as_before(self) -> None:
        result = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=8 * HOUR)
        self.assertEqual(result.lines, ["🌇 Dusk"])
        self.assertIsNone(result.group)

    async def test_the_drift_warning_is_said_once_per_split(self) -> None:
        await self.split_up()
        first = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=9 * HOUR)
        self.assertIn("Tower is 9 hours ahead of Camp.", first.lines)
        again = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=2 * HOUR)
        self.assertNotIn("Tower is 11 hours ahead of Camp.", again.lines)
        self.assertFalse(any("ahead of" in line for line in again.lines))
        # A new split is a new reminder.
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        await self.split_up()
        later = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=9 * HOUR)
        self.assertTrue(any("ahead of" in line for line in later.lines))

    async def test_no_warning_while_the_groups_are_close(self) -> None:
        await self.split_up()
        result = await ui.press(self.fake, GUILD_A, self.a.id, DM, "advance", minutes=8 * HOUR)
        self.assertFalse(any("ahead of" in line for line in result.lines))

    async def test_a_press_names_the_group_it_moved(self) -> None:
        await self.split_up()
        result = await self.press("m10")
        assert result.group is not None
        self.assertEqual((result.group.name, result.group.position), ("Tower", 1))

    async def test_change_can_aim_at_one_group_whatever_is_picked(self) -> None:
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        before, after, acted = await self.clocks.change_named(
            GUILD_A, self.a.id, DM, lambda c: None if c is None else game.advance(c, 5), group=1
        )
        assert before is not None and after is not None and acted is not None
        self.assertEqual((acted.name, after.minute - before.minute), ("Tower", 5))
        self.assertEqual(await self.minutes(), {"Tower": NOON4 + 5, "Camp": NOON4})

    async def test_aiming_at_a_group_that_is_gone_finds_no_clock(self) -> None:
        await self.split_up()
        seen: list[Clock | None] = []

        def look(clock: Clock | None) -> Clock | None:
            seen.append(clock)
            return None

        await self.clocks.change_named(GUILD_A, self.a.id, DM, look, group=3)
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        await self.clocks.change_named(GUILD_A, self.a.id, DM, look, group=1)
        self.assertEqual(seen, [None, None])


class Timers(Party):
    async def start(self, name: str, minutes: int = 30) -> Any:
        return await self.effects.start(GUILD_A, self.a.id, DM, name, None, minutes, False)

    async def test_a_timer_counts_on_the_group_picked_when_it_started(self) -> None:
        await self.split_up()
        await self.press("h1")  # Tower one hour ahead
        bless = await self.start("Bless")
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        haste = await self.start("Haste")
        self.assertEqual((bless.group, bless.ends), (1, NOON4 + 60 + 30))
        self.assertEqual((haste.group, haste.ends), (2, NOON4 + 30))

    async def test_each_timer_is_judged_on_its_own_groups_time(self) -> None:
        await self.split_up()
        await self.start("Bless", 10)
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.start("Haste", 10)
        await self.press("m10")  # Camp reaches its timer; Tower has not moved
        due = await self.effects.claim_due(GUILD_A, self.a.id, (await self.stored()).clock.minute)
        self.assertEqual([e.name for e in due], ["Haste"])
        await self.clocks.pick(GUILD_A, self.a.id, DM, 1)
        await self.press("m10")
        due = await self.effects.claim_due(GUILD_A, self.a.id, (await self.stored()).clock.minute)
        self.assertEqual([e.name for e in due], ["Bless"])

    async def test_a_timer_from_before_the_split_counts_on_the_first_group(self) -> None:
        old = await self.start("Torch", 10)
        self.assertIsNone(old.group)
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("h1")  # Camp moves on; the torch is the first group's
        self.assertEqual(await self.effects.claim_due(GUILD_A, self.a.id, NOON4 + 60), [])
        await self.clocks.pick(GUILD_A, self.a.id, DM, 1)
        await self.press("m10")
        due = await self.effects.claim_due(GUILD_A, self.a.id, NOON4 + 10)
        self.assertEqual([e.name for e in due], ["Torch"])

    async def test_still_going_adds_time_from_its_groups_clock(self) -> None:
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("h1")
        haste = await self.start("Haste", 10)  # Camp: NOON4 + 60 + 10
        await self.clocks.pick(GUILD_A, self.a.id, DM, 1)  # a different group is picked
        longer = await self.effects.extend(GUILD_A, self.a.id, DM, haste.number)
        self.assertEqual(longer.ends, NOON4 + 60 + 10 + 10)

    async def test_going_back_tells_only_that_groups_timers_again(self) -> None:
        await self.split_up()
        await self.start("Bless", 10)
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.start("Haste", 10)
        await self.press("h1")
        await self.effects.claim_due(GUILD_A, self.a.id, NOON4 + 60)  # Haste told
        await self.clocks.pick(GUILD_A, self.a.id, DM, 1)
        await self.press("h1")
        await self.effects.claim_due(GUILD_A, self.a.id, NOON4 + 60)  # Bless told
        await self.clocks.change(
            GUILD_A, self.a.id, DM, lambda c: Clock(NOON4, NOON4)
        )  # Tower back
        due = await self.effects.claim_due(GUILD_A, self.a.id, NOON4 + 1)
        self.assertEqual(due, [])  # Bless is ahead again, so it waits to be told when passed
        told = {e.name: e.told for e in await self.effects.running(GUILD_A, self.a.id)}
        self.assertEqual(told, {"Bless": False, "Haste": True})

    async def test_the_pinned_message_shows_each_group_and_each_timer_for_its_group(self) -> None:
        await self.split_up()
        await self.start("Bless", 120)
        stored = await self.stored()
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        text = await ui.render(self.fake, campaign, stored.clock, stored)
        self.assertIn("Tower: Day 4, 12:00 · Camp: Day 4, 12:00", text)
        self.assertIn("Buttons act on **Tower**.", text)
        self.assertIn("⏳ Bless (Tower): until Day 4, afternoon", text)
        view = ui.clock_view(campaign.id, stored.clock, stored)
        self.assertTrue(any(isinstance(i, ui.ClockPicker) for i in view.children))
        plain = ui.clock_view(campaign.id, stored.clock)
        self.assertFalse(any(isinstance(i, ui.ClockPicker) for i in plain.children))

    async def test_a_timer_that_ended_says_which_group_it_was_for(self) -> None:
        from dmbot.dm_screen import effects as timers

        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.start("Haste", 10)
        await self.press("m10")
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        await timers.announce_due(self.fake, campaign, (await self.stored()).clock.minute)
        [(_, said, _)] = self.fake.posted
        self.assertTrue(said.startswith("Camp: ⏳ Haste"), said)


class Rejoining(Party):
    async def test_rejoin_keeps_the_chosen_time_the_latest_rest_and_moves_the_timers(self) -> None:
        await self.split_up()
        await self.press("h1")
        await self.press("h1")  # Tower: +2h
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("long")  # Camp: +8h and rested
        haste = await self.effects.start(GUILD_A, self.a.id, DM, "Haste", None, 30, False)
        left = haste.ends - (NOON4 + 480)
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        stored = await self.stored()
        self.assertFalse(stored.split)
        self.assertIsNone(stored.picked)
        self.assertEqual(done.kept.name, "Tower")
        self.assertEqual(stored.clock.minute, NOON4 + 120)
        self.assertEqual(
            stored.clock.last_long_rest, NOON4 + 120
        )  # (Camp's rest was later; capped)
        [timer] = await self.effects.running(GUILD_A, self.a.id)
        self.assertIsNone(timer.group)
        self.assertEqual(timer.ends - stored.clock.minute, left)  # the same time left
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM game_clock_groups")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_rejoin_to_the_latest_keeps_the_latest_rest_as_it_was(self) -> None:
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("long")
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 2)
        stored = await self.stored()
        self.assertEqual((stored.clock.minute, stored.clock.last_long_rest), (NOON4 + 480,) * 2)

    async def test_it_goes_back_to_one_clock_that_buttons_and_phrases_use(self) -> None:
        await self.split_up()
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 2)
        await self.press("h1")
        self.assertEqual((await self.stored()).clock.minute, NOON4 + 60)

    async def test_it_must_be_split_to_rejoin(self) -> None:
        with self.assertRaisesRegex(CampaignError, "together already"):
            await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)

    async def test_undo_of_a_split_puts_the_one_clock_back(self) -> None:
        token = await self.split_up()
        self.assertTrue(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))
        stored = await self.stored()
        self.assertFalse(stored.split)
        self.assertEqual(stored.clock.minute, NOON4)
        self.assertFalse(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))  # once

    async def test_undo_of_a_split_refuses_once_a_group_has_moved(self) -> None:
        token = await self.split_up()
        await self.press("m10")
        self.assertFalse(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))
        self.assertTrue((await self.stored()).split)

    async def test_undo_of_a_split_is_for_that_split_and_for_a_dm(self) -> None:
        old = await self.split_up()
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        new = await self.split_up()
        self.assertFalse(await self.clocks.undo_split(GUILD_A, self.a.id, DM, old))
        with self.assertRaises(CampaignError):
            await self.clocks.undo_split(GUILD_A, self.a.id, PLAYER, new)
        self.assertTrue(await self.clocks.undo_split(GUILD_A, self.a.id, DM, new))

    async def test_undo_of_a_split_forgets_the_timers_groups(self) -> None:
        token = await self.split_up()
        await self.effects.start(GUILD_A, self.a.id, DM, "Bless", None, 30, False)
        self.assertTrue(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))
        [timer] = await self.effects.running(GUILD_A, self.a.id)
        self.assertIsNone(timer.group)

    async def test_undo_of_a_rejoin_brings_back_the_groups_times_and_timers(self) -> None:
        await self.split_up("Tower", "Camp", "Hall")
        await self.press("h1")
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("long")
        haste = await self.effects.start(GUILD_A, self.a.id, DM, "Haste", None, 30, False)
        before = await self.stored()
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        self.assertTrue(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, done.token))
        after = await self.stored()
        self.assertEqual(after.groups, before.groups)
        self.assertEqual(after.picked, before.picked)
        [timer] = await self.effects.running(GUILD_A, self.a.id)
        self.assertEqual((timer.group, timer.ends), (2, haste.ends))
        self.assertFalse(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, done.token))

    async def test_undo_of_a_rejoin_refuses_once_the_clock_has_moved(self) -> None:
        await self.split_up()
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        await self.press("m10")
        self.assertFalse(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, done.token))
        self.assertFalse((await self.stored()).split)

    async def test_the_two_undos_are_not_mixed_up(self) -> None:
        token = await self.split_up()
        self.assertFalse(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, token))
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        self.assertFalse(await self.clocks.undo_split(GUILD_A, self.a.id, DM, done.token))


class Review(Party):
    """What the code review of #1607 found."""

    async def test_an_undo_made_before_the_split_does_nothing_once_split(self) -> None:
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        spec = ui.undo_id(result.before, result.after)  # (no group: made on the one clock)
        await self.split_up()
        # Both groups start where the rest left the clock: the old Undo must move neither.
        await ui.ClockUndoButton(self.a.id, spec).callback(self.interaction())
        self.assertEqual(await self.minutes(), {"Tower": NOON4 + 480, "Camp": NOON4 + 480})

    async def test_a_combat_undo_made_before_the_split_does_nothing_once_split(self) -> None:
        result = await self.press("cstart")
        assert result.before is not None and result.after is not None
        spec = ui.combat_undo_id(result.before, result.after)
        await self.press("cend")
        await self.split_up()
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: result.after)
        await ui.CombatUndoButton(self.a.id, spec).callback(self.interaction())
        self.assertTrue(await self.clocks.in_combat(GUILD_A, self.a.id))

    async def test_a_spoken_press_is_refused_under_the_lock_when_split(self) -> None:
        await self.split_up()
        result = await ui.press(self.fake, GUILD_A, self.a.id, DM, "short", refuse_if_split=True)
        self.assertFalse(result.changed)
        self.assertEqual(await self.minutes(), {"Tower": NOON4, "Camp": NOON4})
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        moved = await ui.press(self.fake, GUILD_A, self.a.id, DM, "short", refuse_if_split=True)
        self.assertTrue(moved.changed)

    async def test_undo_of_a_rejoin_keeps_the_time_left_on_a_timer_started_after(self) -> None:
        await self.split_up()
        await self.press("h1")  # Tower +1h; Camp unchanged
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 2)  # keep Camp's earlier time
        late = await self.effects.start(GUILD_A, self.a.id, DM, "Haste", None, 30, False)
        left = late.ends - done.clock.minute
        self.assertTrue(await self.clocks.undo_rejoin(GUILD_A, self.a.id, DM, done.token))
        [timer] = await self.effects.running(GUILD_A, self.a.id)
        stored = await self.stored()
        self.assertEqual(timer.ends - stored.groups[0].clock.minute, left)

    async def test_undo_of_a_split_refuses_when_a_group_started_a_fight(self) -> None:
        token = await self.split_up()
        await self.press("cstart")
        self.assertFalse(await self.clocks.undo_split(GUILD_A, self.a.id, DM, token))

    async def test_a_fight_in_any_group_is_a_fight_at_the_table(self) -> None:
        await self.split_up()
        await self.press("cstart")  # Tower fights
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        self.assertTrue(await self.clocks.in_combat(GUILD_A, self.a.id))

    async def test_rejoin_says_which_other_group_was_in_a_fight(self) -> None:
        await self.split_up()
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)
        await self.press("cstart")
        done = await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        self.assertEqual(done.dropped_fights, ("Camp",))


class OnTheScreen(Party):
    """The form's work, the picker and the buttons, with the Discord side stood in for."""

    async def test_splitting_redraws_the_clock_and_says_it_with_an_undo(self) -> None:
        it = self.interaction()
        self.assertTrue(await ui.run_split(it, self.a.id, ["Tower", "Camp"]))
        texts = [text for _, text, _ in self.fake.posted]
        self.assertIn("Tower: Day 4, 12:00 · Camp: Day 4, 12:00", texts[0])
        self.assertTrue(texts[1].startswith("✂️ Split: **Tower** and **Camp**."))
        view = self.fake.posted[1][2]
        [button] = [i for i in view.children if isinstance(i, ui.SplitUndoButton)]
        undo = self.interaction()
        await button.callback(undo)
        self.assertFalse((await self.stored()).split)

    async def test_a_player_cannot_split_through_the_form(self) -> None:
        it = self.interaction(PLAYER)
        self.assertFalse(await ui.run_split(it, self.a.id, ["Tower", "Camp"]))
        self.assertFalse((await self.stored()).split)

    async def test_the_picker_changes_what_the_buttons_act_on(self) -> None:
        await self.split_up()
        await ui.ClockPicker(self.a.id).callback(self.interaction(values=["2"]))
        self.assertEqual((await self.stored()).picked, 2)

    async def test_a_player_cannot_use_the_picker(self) -> None:
        await self.split_up()
        it = self.interaction(PLAYER, values=["2"])
        await ui.ClockPicker(self.a.id).callback(it)
        self.assertEqual((await self.stored()).picked, 1)

    async def test_rejoin_asks_for_the_time_with_the_latest_first_then_says_it_with_an_undo(
        self,
    ) -> None:
        await self.split_up()
        await self.press("h1")
        stored = await self.stored()
        view = ui.RejoinView(self.a.id, stored.groups)
        labels = [str(i.label) for i in view.children]  # type: ignore[attr-defined]
        self.assertEqual(labels, ["Keep Tower", "Keep Camp"])
        done = await ui.run_rejoin(self.interaction(), self.a.id, 1)
        assert done is not None
        self.assertFalse((await self.stored()).split)
        said = self.fake.posted[-1]
        self.assertIn("Together again at Day 4, 13:00", said[1])
        [button] = [i for i in said[2].children if isinstance(i, ui.SplitUndoButton)]
        await button.callback(self.interaction())
        self.assertTrue((await self.stored()).split)

    async def test_an_old_undo_button_says_so_instead_of_changing_anything(self) -> None:
        token = await self.split_up()
        await self.press("m10")
        it = self.interaction()
        await ui.SplitUndoButton(self.a.id, "split", token).callback(it)
        self.assertTrue((await self.stored()).split)
        it.followup.send.assert_awaited()

    async def test_a_rests_undo_acts_on_the_group_that_rested(self) -> None:
        await self.split_up()
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        await self.clocks.pick(GUILD_A, self.a.id, DM, 2)  # the DM looks at the other group
        it = self.interaction()
        spec = ui.undo_id(result.before, result.after, result.group)
        await ui.ClockUndoButton(self.a.id, spec).callback(it)
        self.assertEqual(await self.minutes(), {"Tower": NOON4, "Camp": NOON4})

    async def test_a_rests_undo_does_nothing_once_the_party_has_rejoined(self) -> None:
        await self.split_up()
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        spec = ui.undo_id(result.before, result.after, result.group)
        await ui.ClockUndoButton(self.a.id, spec).callback(self.interaction())
        self.assertEqual((await self.stored()).clock.minute, NOON4 + 480)

    async def test_the_players_see_one_line_per_group_and_no_exact_time(self) -> None:
        await self.split_up()
        await self.press("h1")
        text = ui.small(ui.players_view(await self.stored()))
        self.assertEqual(
            text,
            "-# 🕰️ The party is split\n-# Tower: Day 4, afternoon\n-# Camp: Day 4, about noon",
        )
        self.assertIsNone(re.search(r"\d:\d\d", text))

    async def test_the_players_hear_which_part_of_the_party_rested(self) -> None:
        await self.split_up()
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET dmbchat_channel_id = 777 WHERE id = %s", (self.a.id,)
            )
        self.fake.channel(777)  # the campaign's chat channel
        result = await self.press("long")
        await ui.update_players(self.fake, campaign, "long", result)
        said = [text for channel, text, _ in self.fake.posted if channel == 777]
        self.assertIn("-# 🕰️ Tower: Long rest done. Day 4, evening.", said)


class SpokenPhrases(Party):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.bot import DMBot
        from dmbot.config import Settings
        from dmbot.consent import ConsentStore
        from dmbot.sessions import SessionStore

        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            clocks=self.clocks,
        )
        self.posted: list[str] = []

        async def post_message(
            channel_id: int, text: str, view: Any = None, *, remember: Any = None
        ) -> None:
            self.posted.append(text)

        self.bot.post_message = post_message  # type: ignore[method-assign]
        self.bot.consent.has_consent = lambda guild_id, user_id: True  # type: ignore[method-assign]
        self.table = SimpleNamespace(
            guild_id=GUILD_A,
            campaign_id=self.a.id,
            listening=True,
            dm_user_ids=frozenset({DM, OTHER_DM}),
            clock_said={},
            clock_suggested_at=None,
            clock_spoken_rest=None,
        )
        self.bot.tables[GUILD_A] = cast(Any, self.table)

    async def minute(self) -> int:
        return int((await self.stored()).clock.minute)

    async def test_nothing_moves_while_split_and_the_dm_is_told_once(self) -> None:
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        for line in ("you take a short rest", "we take a long rest", "you take a long rest"):
            await self.bot._note_clock_phrase(cast(Any, self.table), DM, line)
        self.assertEqual(await self.minute(), NOON4)
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual([g.clock.minute for g in stored.groups], [NOON4, NOON4])
        told = [t for t in self.posted if "The party is split, so I didn't move the clock" in t]
        self.assertEqual(
            told,
            ["The party is split, so I didn't move the clock. Use the buttons for each group."],
        )

    async def test_a_fight_said_aloud_moves_nothing_while_split(self) -> None:
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        fight = combat_phrases.find_combat("roll initiative")
        assert fight is not None
        await self.bot._note_combat_phrase(cast(Any, self.table), DM, "roll initiative", fight)
        self.assertFalse(await self.clocks.in_combat(GUILD_A, self.a.id))
        self.assertEqual(len([t for t in self.posted if "didn't move the clock" in t]), 1)

    async def test_the_line_is_said_again_for_a_new_split(self) -> None:
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "you take a short rest")
        await self.clocks.rejoin(GUILD_A, self.a.id, DM, 1)
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "you take a short rest")
        self.assertEqual(await self.minute(), NOON4 + 60)  # one clock again: it moves
        self.table.clock_said.clear()
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "you take a long rest")
        # Once for each split, and not again for the second phrase of the first.
        self.assertEqual(len([t for t in self.posted if "didn't move the clock" in t]), 2)

    async def test_a_players_words_never_say_it(self) -> None:
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        await self.bot._note_clock_phrase(cast(Any, self.table), PLAYER, "we take a long rest")
        self.assertEqual(self.posted, [])


class Backups(Party):
    async def test_a_split_party_survives_a_backup_with_its_timers(self) -> None:
        await self.split_up("Tower", "Camp", "Hall")
        await self.press("h1")
        await self.clocks.pick(GUILD_A, self.a.id, DM, 3)
        await self.effects.start(GUILD_A, self.a.id, DM, "Haste", None, 30, False)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        stored = await self.clocks.get(GUILD_B, restored.id)
        assert stored is not None
        self.assertEqual(
            [(g.position, g.name, g.clock.minute) for g in stored.groups],
            [(1, "Tower", NOON4 + 60), (2, "Camp", NOON4), (3, "Hall", NOON4)],
        )
        self.assertEqual(stored.picked, 3)
        self.assertFalse(stored.phrase_told or stored.drift_told)
        [timer] = await self.effects.running(GUILD_B, restored.id)
        self.assertEqual(timer.group, 3)

    async def test_a_clock_that_is_not_split_dumps_just_what_it_did_before(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            rows = await ClockSection().dump(conn, GUILD_A, self.a.id)
        # (the clock's log, #3091, rides along: the same two numbers, plus the moment it was set)
        (log,) = rows[0].pop("log")
        self.assertEqual((log["position"], log["minute"]), (None, NOON4))
        self.assertGreater(log["at"], 0)
        self.assertEqual(rows, [{"minute": NOON4, "last_long_rest": NOON4}])

    async def test_a_backup_from_before_groups_still_restores(self) -> None:
        checked = ClockSection().check([{"minute": 10, "last_long_rest": 5}])
        self.assertEqual(checked, Restored(Clock(10, 5, None)))

    async def test_a_damaged_group_in_a_backup_is_refused(self) -> None:
        good = {"position": 1, "name": "Tower", "minute": 10, "last_long_rest": 0}
        second = {"position": 2, "name": "Camp", "minute": 10, "last_long_rest": 0}
        main = {"minute": 10, "last_long_rest": 0}
        ClockSection().check([{**main, "groups": [good, second], "picked": 2}])  # fine
        for bad in (
            {**main, "groups": [good], "picked": 1},  # one group is not a split
            {**main, "groups": [good, second]},  # no picked group
            {**main, "groups": [good, second], "picked": 3},  # picked one that is not there
            {**main, "groups": [good, {**second, "position": 1}], "picked": 1},
            {**main, "groups": [good, {**second, "name": "tower"}], "picked": 1},
            {**main, "groups": [good, {**second, "name": ""}], "picked": 1},
            {**main, "groups": [good, {**second, "name": "x" * 21}], "picked": 1},
            {**main, "groups": [good, {**second, "position": 4}], "picked": 1},
            {**main, "groups": [good, {**second, "minute": -1}], "picked": 1},
            {**main, "groups": [good, {**second, "last_long_rest": 99}], "picked": 1},
            {**main, "groups": [good, {**second, "extra": 1}], "picked": 1},
            {**main, "groups": [good, "x"], "picked": 1},
            {**main, "groups": [good, second, second, second], "picked": 1},
            {**main, "picked": 1},  # a pick with no groups
            {**main, "groups": "x"},
        ):
            with self.assertRaises(CampaignError, msg=str(bad)):
                ClockSection().check([bad])

    async def test_a_damaged_timer_group_in_a_backup_is_refused(self) -> None:
        good = {
            "name": "Haste", "target": None, "minutes": 10, "concentration": False,
            "ends": 20, "told": False, "group": None,
        }  # fmt: skip
        EffectsSection().check([good])
        for bad in (0, 4, "1", True, 1.0):
            with self.assertRaises(CampaignError, msg=str(bad)):
                EffectsSection().check([{**good, "group": bad}])

    async def test_the_groups_stay_in_their_server_and_go_with_the_campaign(self) -> None:
        await self.split_up()
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM game_clock_groups")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # another server sees none
        await self.campaigns.delete(GUILD_A, self.a.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM game_clock_groups")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class Migration(Party):
    async def test_a_clock_row_and_a_timer_from_before_0109_come_through_unchanged(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0109"]
        await self.db.close()  # start again from a schema as it was before 0109
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = type(self.campaigns)(self.db)
        campaign = await campaigns.create(GUILD_A, "Alpha", DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " updated_at) VALUES (%s, %s, %s, %s, 1)",
                (GUILD_A, campaign.id, NOON4, NOON4 - 5),
            )
            await conn.execute(
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, minutes,"
                " ends_minute) VALUES (%s, %s, 1, 'Bless', 10, %s)",
                (GUILD_A, campaign.id, NOON4 + 10),
            )
        await self.db.migrate()
        stored = await ClockStore(self.db).get(GUILD_A, campaign.id)
        assert stored is not None
        self.assertEqual((stored.clock.minute, stored.clock.last_long_rest), (NOON4, NOON4 - 5))
        self.assertFalse(stored.split)
        self.assertIsNone(stored.picked)
        [timer] = await EffectStore(self.db).running(GUILD_A, campaign.id)
        self.assertEqual((timer.name, timer.ends, timer.group), ("Bless", NOON4 + 10, None))
        # …and the old clock can be split afterwards.
        await ClockStore(self.db).split(GUILD_A, campaign.id, DM, ["Tower", "Camp"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
