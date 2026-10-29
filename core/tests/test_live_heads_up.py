"""The first live heads-up (#2068): a character the DM confirmed dead is voiced at the table.
Invented fixtures only ("Captain Rook"). The rule is pure; the bot's wiring and the DM's buttons
are tried with stand-ins, so no Discord and no database."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot import bot as botmod
from dmbot.dm_screen import levels
from dmbot.memory import live_heads_up as lhu
from dmbot.memory.live_heads_up import LiveSession
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import (
    ALIVE,
    CONFIRMED,
    DEAD,
    DM,
    NPC,
    PLAYER_CHARACTER,
    PROPOSED,
    Alias,
    Entity,
    State,
    name_key,
)
from dmbot.transcript.models import NARRATING
from dmbot.transcript.topics import Waiting
from dmbot.ui import live_heads_up as ui

GUILD, CAMPAIGN, SPEAKER, DM_USER = 1, "c" * 32, 7, 8
ROOK_ID = "a" * 32


def rook(**kw: Any) -> Entity:
    base: dict[str, Any] = {
        "id": ROOK_ID, "type": "character", "name": "Captain Rook", "description": "",
        "status": CONFIRMED, "merged_into": None, "source": DM, "created_at": 1, "role": NPC,
    }  # fmt: skip
    return Entity(**{**base, **kw})


def condition(name: str = DEAD, since: int | None = 1000) -> State:
    return State("s1", ROOK_ID, name, since, DM, 2)


class TheRule(unittest.TestCase):
    def ask(self, entity: Entity | None = None, state: Any = "dead", **kw: Any) -> Any:
        args: dict[str, Any] = {"ready": True, "session": LiveSession(), "now": 100.0}
        args.update(kw)
        return lhu.heads_up(
            entity if entity is not None else rook(),
            condition() if state == "dead" else state,
            **args,
        )

    def test_a_dead_confirmed_character_gives_one_heads_up(self) -> None:
        session = LiveSession()
        first = self.ask(session=session, now=100.0)
        self.assertEqual((first.name, first.since, first.level), ("Captain Rook", 1000, "likely"))
        self.assertIsNone(self.ask(session=session, now=101.0))  # the cooldown is used
        self.assertIsNone(self.ask(session=session, now=100.0 + lhu.COOLDOWN_S - 1))
        self.assertIsNotNone(self.ask(session=session, now=100.0 + lhu.COOLDOWN_S))

    def test_nothing_for_an_alive_proposed_merged_or_unconfirmed_entry(self) -> None:
        self.assertIsNone(self.ask(state=condition(ALIVE)))
        self.assertIsNone(self.ask(state=None))
        self.assertIsNone(self.ask(rook(status=PROPOSED)))
        self.assertIsNone(self.ask(rook(merged_into="b" * 32)))
        self.assertIsNone(self.ask(rook(type="item")))
        self.assertIsNone(lhu.heads_up(None, condition(), ready=True, session=LiveSession(), now=1))

    def test_nothing_below_critical_mass(self) -> None:
        self.assertIsNone(self.ask(ready=False))

    def test_a_silenced_entry_stays_quiet_for_the_session(self) -> None:
        session = LiveSession()
        lhu.keep_quiet(session, ROOK_ID)
        self.assertIsNone(self.ask(session=session))

    def test_the_cooldown_is_a_setting(self) -> None:
        session = LiveSession()
        self.assertIsNotNone(self.ask(session=session, now=0.0, cooldown_s=0.0))
        self.assertIsNotNone(self.ask(session=session, now=0.0, cooldown_s=0.0))

    def test_quiet_has_no_live_warnings(self) -> None:
        self.assertFalse(levels.allows("quiet", levels.WARNING))
        self.assertTrue(levels.allows("normal", levels.WARNING))
        self.assertTrue(levels.allows("chatty", levels.WARNING))

    def test_the_words_are_plain_and_quote_nothing(self) -> None:
        found = self.ask()
        self.assertEqual(
            ui.heads_up_text(found, 4),
            "⚠️ **Heads-up: Captain Rook** died in Session 4. They were just voiced talking.",
        )
        self.assertIn("died earlier.", ui.heads_up_text(found, None))


def lookup_with(*entities: Entity) -> Any:
    """A real lookup, so the label is resolved by the same rule that made it."""
    aliases = tuple(
        Alias(
            e.id[:16] + "k" * 16,
            e.id,
            e.name,
            name_key(e.name),
            "full",
            None,
            False,
            e.status,
            (),
            DM,
            0,
        )
        for e in entities
    )
    return CampaignLookup.build(LookupData(1, entities, aliases, (), ()))


class Wiring(unittest.IsolatedAsyncioTestCase):
    """`DMBot._live_heads_up` and the hook in `_save_entity`, on a stand-in for the bot."""

    def make(self, **over: Any) -> tuple[Any, Any]:
        table = SimpleNamespace(
            guild_id=GUILD, campaign_id=CAMPAIGN, screen_channel_id=55, screen_level="normal",
            name_lookup=lookup_with(rook()), live_heads_up=LiveSession(), started_at=5000,
            save_lock=asyncio.Lock(), unsaved=SimpleNamespace(set_entity=lambda *a: True),
            transcript_session_id=None,
        )  # fmt: skip
        consenting = {SPEAKER}
        bot = SimpleNamespace(
            memory=SimpleNamespace(states=AsyncMock(return_value={ROOK_ID: condition()})),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=CAMPAIGN))),
            consent=SimpleNamespace(has_consent=lambda g, u: u in consenting),
            plan_allows=AsyncMock(return_value=True),
            settings=SimpleNamespace(
                live_heads_up=True, live_heads_up_cooldown_s=300.0, continuity=object()
            ),
            transcripts=SimpleNamespace(
                sessions=AsyncMock(return_value=[SimpleNamespace(started_at=1000, number=4)])
            ),
            post=AsyncMock(),
            tables={GUILD: table},
            consenting=consenting,
            tracked=[],
            _label_known=lambda *a: None,
        )
        bot._track = lambda work, name: (bot.tracked.append(work), work.close())
        bot._live_heads_up = lambda *a: asyncio.sleep(0)
        bot.__dict__.update(over)
        return bot, table

    async def run_it(
        self, bot: Any, table: Any, ready: bool = True, label: str = "Captain Rook"
    ) -> None:
        store = MagicMock()
        store.return_value.is_ready = AsyncMock(return_value=ready)
        with patch.object(botmod, "ContinuityStore", store):
            await botmod.DMBot._live_heads_up(bot, table, SPEAKER, label)

    async def test_a_voiced_dead_character_posts_one_heads_up_to_the_dm_screen(self) -> None:
        bot, table = self.make()
        await self.run_it(bot, table)
        bot.post.assert_awaited_once()
        channel, text = bot.post.await_args.args
        self.assertEqual(channel, 55)
        self.assertIn("**Heads-up: Captain Rook** died in Session 4", text)
        self.assertIsNotNone(bot.post.await_args.kwargs["view"])
        await self.run_it(bot, table)  # said again at once: the cooldown holds
        self.assertEqual(bot.post.await_count, 1)

    async def assertNothing(self, bot: Any, table: Any, **kw: Any) -> None:
        await self.run_it(bot, table, **kw)
        bot.post.assert_not_awaited()

    async def test_none_below_critical_mass(self) -> None:
        bot, table = self.make()
        await self.assertNothing(bot, table, ready=False)

    async def test_none_on_quiet(self) -> None:
        bot, table = self.make()
        table.screen_level = "quiet"
        await self.assertNothing(bot, table)

    async def test_none_when_the_plan_has_no_story_memory(self) -> None:
        bot, table = self.make(plan_allows=AsyncMock(return_value=False))
        await self.assertNothing(bot, table)
        self.assertEqual(bot.plan_allows.await_args.kwargs, {"fail_open": False})

    async def test_none_when_the_character_is_alive_or_only_proposed(self) -> None:
        bot, table = self.make()
        bot.memory.states = AsyncMock(return_value={ROOK_ID: condition(ALIVE)})
        await self.assertNothing(bot, table)
        bot2, table2 = self.make()
        table2.name_lookup = lookup_with(rook(status=PROPOSED))
        await self.assertNothing(bot2, table2)

    async def test_none_for_a_name_that_is_no_character_in_the_story(self) -> None:
        bot, table = self.make()
        await self.assertNothing(bot, table, label="Somebody Else")

    async def test_none_when_the_speaker_stopped_before_or_during(self) -> None:
        bot, table = self.make()
        bot.consenting.discard(SPEAKER)
        await self.assertNothing(bot, table)
        bot2, table2 = self.make()

        async def states_then_stop(*a: Any) -> Any:
            bot2.consenting.discard(SPEAKER)  # they press Stop while the story is read
            return {ROOK_ID: condition()}

        bot2.memory.states = states_then_stop
        await self.assertNothing(bot2, table2)

    async def test_none_when_another_entry_shares_the_name(self) -> None:
        bot, table = self.make()
        table.name_lookup = lookup_with(rook(), rook(id="b" * 32, role=PLAYER_CHARACTER))
        await self.assertNothing(bot, table)  # which Rook was talking? DMbot does not guess
        bot2, table2 = self.make()
        table2.name_lookup = lookup_with(rook(), rook(id="c" * 32, status=PROPOSED))
        await self.assertNothing(bot2, table2)

    async def test_a_post_that_fails_does_not_use_up_the_cooldown(self) -> None:
        bot, table = self.make(post=AsyncMock(return_value=False))
        await self.run_it(bot, table)
        bot.post.assert_awaited_once()
        self.assertIsNone(table.live_heads_up.last_at)
        bot.post = AsyncMock(return_value=True)
        await self.run_it(bot, table)  # the next try can still say it
        bot.post.assert_awaited_once()

    async def test_a_level_turned_to_quiet_while_it_works_stops_the_post(self) -> None:
        bot, table = self.make()

        async def states_then_quiet(*a: Any) -> Any:
            table.screen_level = "quiet"
            return {ROOK_ID: condition()}

        bot.memory.states = states_then_quiet
        await self.assertNothing(bot, table)
        self.assertIsNone(table.live_heads_up.last_at)

    async def test_none_when_the_session_has_ended(self) -> None:
        bot, table = self.make()
        bot.tables.clear()
        await self.assertNothing(bot, table)

    async def test_the_switch_and_narration_are_checked_where_the_label_is_saved(self) -> None:
        for on, label, expected in ((True, "Captain Rook", 1), (False, "Captain Rook", 0),
                                    (True, NARRATING, 0), (True, "", 0)):  # fmt: skip
            bot, table = self.make()
            bot.settings.live_heads_up = on
            await botmod.DMBot._save_entity(bot, table, Waiting(SPEAKER, 1, "", 0.0), label)
            self.assertEqual(len(bot.tracked), expected, (on, label))

    async def test_a_failure_costs_the_heads_up_and_never_the_line(self) -> None:
        bot, table = self.make(campaigns=SimpleNamespace(get=AsyncMock(side_effect=OSError)))
        await self.assertNothing(bot, table)


class Buttons(unittest.IsolatedAsyncioTestCase):
    def make(self, user: int = DM_USER) -> tuple[Any, Any, Any]:
        live = LiveSession()
        table = SimpleNamespace(campaign_id=CAMPAIGN, live_heads_up=live, started_at=5000)
        memory = SimpleNamespace(
            entity=AsyncMock(return_value=rook()),
            set_condition=AsyncMock(),
            states=AsyncMock(return_value={ROOK_ID: condition()}),
        )
        bot = SimpleNamespace(
            memory=memory,
            tables={GUILD: table},
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(id=CAMPAIGN, dm_user_ids=[DM_USER]))
            ),
        )
        done = {"now": False}

        async def defer(*a: Any, **k: Any) -> None:
            done["now"] = True

        response = SimpleNamespace(
            is_done=lambda: done["now"], send_message=AsyncMock(), defer=defer
        )
        interaction = SimpleNamespace(
            client=bot, guild=SimpleNamespace(id=GUILD), user=SimpleNamespace(id=user),
            response=response, type=None, edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )  # fmt: skip
        return interaction, bot, live

    async def test_alive_after_all_records_a_dated_condition(self) -> None:
        interaction, bot, _live = self.make()
        await ui.answer(interaction, ui.ALIVE_AFTER_ALL, CAMPAIGN, ROOK_ID)
        bot.memory.set_condition.assert_awaited_once_with(
            GUILD, CAMPAIGN, ROOK_ID, ALIVE, source=DM, from_session_at=5000
        )
        text = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Captain Rook", text)
        self.assertIsNone(interaction.edit_original_response.await_args.kwargs["view"])

    async def test_a_stale_button_never_overwrites_a_newer_word_of_the_dms(self) -> None:
        interaction, bot, _live = self.make()
        bot.memory.states = AsyncMock(return_value={ROOK_ID: condition("missing")})
        await ui.answer(interaction, ui.ALIVE_AFTER_ALL, CAMPAIGN, ROOK_ID)
        bot.memory.set_condition.assert_not_awaited()
        text = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Nothing changed", text)

    async def test_a_button_from_another_campaigns_session_dates_nothing_with_it(self) -> None:
        interaction, bot, _live = self.make()
        bot.tables[GUILD].campaign_id = "d" * 32  # the running table is another campaign
        await ui.answer(interaction, ui.ALIVE_AFTER_ALL, CAMPAIGN, ROOK_ID)
        bot.memory.set_condition.assert_awaited_once_with(
            GUILD, CAMPAIGN, ROOK_ID, ALIVE, source=DM, from_session_at=None
        )

    async def test_a_failed_write_tells_the_dm_and_leaves_the_buttons(self) -> None:
        interaction, bot, _live = self.make()
        bot.memory.set_condition = AsyncMock(side_effect=OSError)
        await ui.answer(interaction, ui.ALIVE_AFTER_ALL, CAMPAIGN, ROOK_ID)
        self.assertIn("couldn't record", interaction.followup.send.await_args.args[0])
        interaction.edit_original_response.assert_not_awaited()  # the buttons stay

    async def test_not_really_them_and_ignore_write_nothing_and_keep_it_quiet(self) -> None:
        for action in (ui.NOT_THEM, ui.IGNORE):
            interaction, bot, live = self.make()
            await ui.answer(interaction, action, CAMPAIGN, ROOK_ID)
            bot.memory.set_condition.assert_not_awaited()
            self.assertIn(ROOK_ID, live.quiet)
            self.assertIsNone(lhu.heads_up(rook(), condition(), ready=True, session=live, now=1.0))

    async def test_someone_who_is_not_the_dm_is_refused_and_nothing_changes(self) -> None:
        for action in (ui.ALIVE_AFTER_ALL, ui.NOT_THEM, ui.IGNORE):
            interaction, bot, live = self.make(user=99)
            await ui.answer(interaction, action, CAMPAIGN, ROOK_ID)
            bot.memory.set_condition.assert_not_awaited()
            self.assertEqual(live.quiet, set())
            sent = interaction.response.send_message.await_args
            self.assertEqual(sent.args[0], ui.NOT_THE_DM)

    async def test_the_buttons_carry_the_campaign_and_the_entry(self) -> None:
        view = ui.heads_up_view(CAMPAIGN, ROOK_ID)
        ids = [cast(Any, item).item.custom_id for item in view.children]
        self.assertEqual(ids, [f"dmbot:liveup:{a}:{CAMPAIGN}:{ROOK_ID}" for a in ("a", "n", "i")])
        for custom_id in ids:
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(
                ui.LiveHeadsUpButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            )
