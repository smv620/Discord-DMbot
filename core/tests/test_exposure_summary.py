"""The after-session summary of secrets said at the table (#2410, slice 2): the text and buttons,
showing exposures in a change each, the step that tells the DMs first and shows afterwards, and
↩️ Recant. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.memory.exposures import Exposure, ExposureStore, describe
from dmbot.memory.models import CONFIRMED, spelling_key
from dmbot.ui.exposure_summary import (
    BUSY,
    GONE,
    MAX_LISTED,
    NOTHING,
    STUCK,
    RecantButton,
    names_line,
    summary_text,
    summary_view,
)
from dmbot.ui.secret_aloud import NOT_YOURS
from tests.test_memory_store import GUILD_A, MemoryTest

DM_ID = 7
CID = "c" * 32


class Text(unittest.TestCase):
    def test_the_message_lists_numbers_the_items_and_says_what_the_button_does(self) -> None:
        text = summary_text(["Auril (a name)", "Mira and Rook (knows)"], 0)
        self.assertIn("1. Auril (a name)", text)
        self.assertIn("2. Mira and Rook (knows)", text)
        self.assertIn("↩️ Recant", text)
        self.assertNotIn("more", text)

    def test_past_the_cap_it_says_how_many_more_and_that_they_come_next_time(self) -> None:
        text = summary_text(["A"] * MAX_LISTED, 3)
        self.assertIn("and 3 more", text)
        self.assertIn("next summary", text)

    def test_ten_long_items_still_fit_in_a_discord_message(self) -> None:
        text = summary_text(
            ["Name " + "x" * 400] * MAX_LISTED, 40, campaign_name="C" * 200, private=True
        )
        self.assertLessEqual(len(text), 2000)

    def test_a_private_copy_says_why_and_names_the_campaign(self) -> None:
        text = summary_text(["Auril (a name)"], 0, campaign_name="Frost*", private=True)
        self.assertIn("Sent to you alone", text)
        self.assertIn("Frost\\*", text)

    def test_one_button_for_each_item_five_to_a_row_and_no_more_than_the_cap(self) -> None:
        view = summary_view(list(range(1, 15)), CID, GUILD_A)
        self.assertEqual(len(view.children), MAX_LISTED + 1)  # and the mark every variant has
        self.assertEqual(view.children[-1].item.custom_id, f"dmbot:xs:{CID}:{GUILD_A}")  # type: ignore[attr-defined]
        rows = [c.item.row for c in view.children if isinstance(c, RecantButton)]
        self.assertEqual((rows.count(0), rows.count(1)), (5, 5))
        first = view.children[0]
        assert isinstance(first, RecantButton)
        first = first.item
        self.assertEqual(first.custom_id, f"dmbot:xr:1:{CID}:{GUILD_A}")
        self.assertLessEqual(len(first.custom_id or ""), 100)
        self.assertEqual(first.label, "↩️ Recant 1")

    def test_new_names_ride_in_the_same_message_with_their_own_button(self) -> None:
        text = summary_text(["Auril (a name)"], 0, new_names=["Bryn", "Oskar"])
        self.assertIn("🆕 **2 new names to check:** **Bryn**, **Oskar**", text)
        view = summary_view([1], CID, GUILD_A, new_names=True)
        check: Any = next(
            c
            for c in view.children
            if c.item.custom_id.startswith("dmbot:names:review:")  # type: ignore[attr-defined]
        )
        self.assertEqual(check.item.custom_id, f"dmbot:names:review:{CID}")
        self.assertEqual(check.item.row, 2)
        private: Any = summary_view([1], CID, GUILD_A, new_names=True, private=True).children[-2]
        self.assertEqual(private.item.custom_id, f"dmbot:names:recap:{GUILD_A}:{CID}")
        plain = summary_view([1], CID, GUILD_A)
        self.assertEqual(len(plain.children), 2)  # a Recant and the mark; no names button
        self.assertNotIn("🆕", summary_text(["A"], 0))

    def test_things_to_check_are_spelled_out_for_the_dm_and_dropped_if_too_long(self) -> None:
        lines = ["**Bram** carries **Heartseeker**? _(you said)_"] * 2
        text = summary_text([], 0, claims_waiting=4, claim_lines=lines)
        self.assertIn("• **Bram** carries", text)
        self.assertIn("…and 2 more in the check.", text)
        long = ["x" * 600] * 5
        full = summary_text(
            ["Name " + "y" * 400] * MAX_LISTED, 0, new_names=["N" * 300] * 5,
            claims_waiting=9, claim_lines=long,
        )  # fmt: skip
        self.assertLessEqual(len(full), 2000)
        self.assertNotIn("xxx", full)  # the words went; the count stays
        self.assertIn("9 things said at the table to check", full)

    def test_a_long_list_of_long_names_still_fits(self) -> None:
        line = names_line(["N" * 300] * 9)
        self.assertIn("and 4 more", line)
        text = summary_text(
            ["Name " + "x" * 400] * MAX_LISTED,
            40,
            campaign_name="C" * 200,
            private=True,
            new_names=["N" * 300] * 9,
        )
        self.assertLessEqual(len(text), 2000)


def MESSAGE() -> Any:  # a posted message, as the bot keeps it
    return SimpleNamespace(id=1, edit=AsyncMock())


class Base(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ExposureStore(self.db)

    async def secret_name(self, name: str = "Auril") -> tuple[str, str]:
        owner = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        alias = await self.memory.add_alias(
            GUILD_A, self.c, owner, name, kind="nickname", source="dm", status=CONFIRMED,
            secret=True,
        )  # fmt: skip
        return owner, alias.value.id

    async def secret_link(self) -> str:
        a = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        b = await self.add("Rook", type="character", role="npc", status=CONFIRMED)
        written = await self.relate(a, "knows", b, status=CONFIRMED, secret=True)
        return str(written.value[0].id)

    async def is_secret(self, alias_id: str) -> bool:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT secret FROM memory_aliases WHERE id = %s", (alias_id,))
            row = await cur.fetchone()
        assert row is not None
        return bool(row["secret"])


class Showing(Base):
    async def test_a_name_is_shown_in_one_change_and_undo_hides_it_again(self) -> None:
        _, alias = await self.secret_name()
        written = await self.memory.reveal_exposed(
            GUILD_A, self.c, spellings=[spelling_key("Auril")], relation_ids=[]
        )
        self.assertEqual(written.value, (1, 0))
        self.assertFalse(await self.is_secret(alias))
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch, source="dm")
        self.assertTrue(await self.is_secret(alias))

    async def test_a_connection_is_shown_to_the_party_and_undo_takes_it_back(self) -> None:
        rid = await self.secret_link()
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c), [])
        written = await self.memory.reveal_exposed(
            GUILD_A, self.c, spellings=[], relation_ids=[rid]
        )
        self.assertEqual(written.value, (0, 1))
        self.assertEqual(
            [b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)], [rid]
        )
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch, source="dm")
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c), [])

    async def test_what_is_not_secret_or_not_confirmed_changes_nothing(self) -> None:
        written = await self.memory.reveal_exposed(
            GUILD_A, self.c, spellings=["nobody"], relation_ids=["f" * 32]
        )
        self.assertEqual((written.value, written.batch), ((0, 0), None))

    async def test_every_name_with_the_spelling_is_shown_and_others_are_not(self) -> None:
        _, one = await self.secret_name("Auril")
        _, two = await self.secret_name("auril")
        _, other = await self.secret_name("Vex")
        await self.memory.reveal_exposed(
            GUILD_A, self.c, spellings=[spelling_key("Auril")], relation_ids=[]
        )
        self.assertEqual([await self.is_secret(x) for x in (one, two, other)], [False, False, True])

    async def test_the_rows_move_once_and_only_from_the_right_state(self) -> None:
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "auril", None, 2)])
        (row,) = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "applied", 55), 0)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "applying"), 1)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "applying"), 0)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "applied", 55), 1)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "applied", 66), 0)
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual((got.status, got.batch), ("applied", 55))
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "recanted"), 1)
        self.assertEqual(await self.store.mark(GUILD_A, self.c, [row.id], "recanted"), 0)
        self.assertIsNone(await self.store.one(GUILD_A, "0" * 32, row.id))  # another campaign's

    async def test_describing_gives_plain_words_and_leaves_out_what_is_no_longer_secret(
        self,
    ) -> None:
        await self.secret_name("Auril")
        rid = await self.secret_link()
        found = [
            Exposure("name", spelling_key("Auril"), None, 3),
            Exposure("connection", None, rid, 1),
            Exposure("name", "gone name", None, 1),
        ]
        await self.store.record(GUILD_A, self.c, 100, found)
        rows = await self.store.waiting(GUILD_A, self.c)
        got = [text for _, text in await describe(self.memory, GUILD_A, self.c, rows)]
        self.assertEqual(got, ["Auril (a name)", "Mira and Rook (knows)"])


def campaign(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "guild_id": GUILD_A, "id": CID, "name": "Frost", "dm_user_ids": frozenset({DM_ID}),
        "dm_screen_visibility": "private",
    }  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


class StepBase(Base):
    def bot(self, camp: Any, *, posted: bool = True, dm: str = "sent") -> Any:
        return SimpleNamespace(
            memory=self.memory,
            exposures=self.store,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
            settings=SimpleNamespace(site_url="https://example.org"),
            post=AsyncMock(return_value=posted),
            post_message=AsyncMock(return_value=MESSAGE() if posted else None),
            sidebar_send_dm=AsyncMock(return_value=dm),
            lookup=None,
        )

    def table(self) -> Any:
        return SimpleNamespace(
            guild_id=GUILD_A,
            campaign_id=self.c,
            screen_channel_id=55,
            new_names=[],
            claims_waiting=0,
            claim_preview=[],
        )

    async def waiting_secret(self) -> str:
        return await self.waiting_secret_named("Auril")

    async def waiting_secret_named(self, name: str) -> str:
        _, alias = await self.secret_name(name)
        await self.store.record(
            GUILD_A, self.c, 100, [Exposure("name", spelling_key(name), None, 2)]
        )
        return alias


class TheStep(StepBase):
    async def test_the_dms_are_told_first_and_then_it_is_shown_with_a_recant_button(self) -> None:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        order: list[str] = []

        async def post(*args: Any, **kw: Any) -> Any:
            order.append(f"post, secret then: {await self.is_secret(alias)}")
            return MESSAGE()

        bot.post_message = post
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertEqual(order, ["post, secret then: True"])  # still secret when told
        self.assertFalse(await self.is_secret(alias))  # and shown after
        (row,) = [r for r in [await self.store.one(GUILD_A, self.c, 1)] if r]
        self.assertEqual(row.status, "applied")
        self.assertIsNotNone(row.batch)

    async def test_new_names_ride_along_and_are_told_once(self) -> None:
        await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        table = self.table()
        table.new_names = ["Bryn Shander"]
        await DMBot.send_exposure_summary(bot, table)
        text, view = bot.post_message.await_args.args[1:3]
        self.assertIn("🆕 **1 new name to check:** **Bryn Shander**", text)
        self.assertTrue(any(getattr(c, "item", c).row == 2 for c in view.children))
        self.assertEqual(table.new_names, [])  # nobody hears them twice

    async def test_a_private_copy_carries_the_names_button_that_works_without_a_server(
        self,
    ) -> None:
        await self.waiting_secret()
        bot = self.bot(campaign(id=self.c, dm_screen_visibility="open"))
        table = self.table()
        table.new_names = ["Bryn Shander"]
        await DMBot.send_exposure_summary(bot, table)
        view = bot.sidebar_send_dm.await_args.args[2]
        ids = [getattr(c, "item", c).custom_id for c in view.children]
        self.assertIn(f"dmbot:names:recap:{GUILD_A}:{self.c}", ids)
        self.assertNotIn(f"dmbot:names:review:{self.c}", ids)

    async def test_a_co_dm_who_could_not_be_reached_still_gets_the_names_from_the_names_step(
        self,
    ) -> None:
        await self.waiting_secret()
        two = campaign(id=self.c, dm_screen_visibility="open", dm_user_ids=frozenset({7, 8}))
        bot = self.bot(two)
        sent = {7: "sent", 8: "forbidden"}
        bot.sidebar_send_dm = AsyncMock(side_effect=lambda dm_id, *a: sent[dm_id])
        table = self.table()
        table.new_names = ["Bryn Shander"]
        await DMBot.send_exposure_summary(bot, table)
        self.assertEqual(table.new_names, ["Bryn Shander"])  # the names step posts them
        await self.waiting_secret_named("Mira")  # another one to tell, now to every DM
        bot.sidebar_send_dm = AsyncMock(return_value="sent")
        await DMBot.send_exposure_summary(bot, table)
        self.assertEqual(table.new_names, [])  # every DM was reached: told once

    async def test_the_count_of_things_to_check_rides_on_the_screen_message(self) -> None:
        await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        table = self.table()
        table.claims_waiting = 3
        await DMBot.send_exposure_summary(bot, table)
        text, view = bot.post_message.await_args.args[1:3]
        self.assertIn("🔗 **3 things said at the table to check.**", text)
        self.assertIn(
            f"dmbot:claims:review:{self.c}",
            [getattr(c, "item", c).custom_id for c in view.children],
        )
        self.assertEqual(table.claims_waiting, 0)  # told: the "new connections" step stays quiet

    async def test_a_private_copy_leaves_the_check_to_the_screen_post(self) -> None:
        await self.waiting_secret()
        bot = self.bot(campaign(id=self.c, dm_screen_visibility="open"))
        table = self.table()
        table.claims_waiting = 3
        await DMBot.send_exposure_summary(bot, table)
        sent_text, sent_view = bot.sidebar_send_dm.await_args.args[1:3]
        self.assertIn("Open 🗣️ Check what was said on your DM screen", sent_text)  # no button here
        ids = [getattr(c, "item", c).custom_id for c in sent_view.children]
        self.assertNotIn(f"dmbot:claims:review:{self.c}", ids)
        self.assertEqual(table.claims_waiting, 3)  # posted by the "new connections" step

    async def test_new_names_wait_for_their_own_post_when_the_summary_cannot_go(self) -> None:
        await self.waiting_secret()
        bot = self.bot(campaign(id=self.c), posted=False)
        table = self.table()
        table.new_names = ["Bryn Shander"]
        await DMBot.send_exposure_summary(bot, table)
        self.assertEqual(table.new_names, ["Bryn Shander"])  # the "new names" step posts them

    async def test_without_a_secret_the_message_still_goes_for_names_and_things_to_check(
        self,
    ) -> None:
        bot = self.bot(campaign(id=self.c))
        table = self.table()
        table.new_names = ["Bryn Shander"]
        table.claims_waiting = 2
        table.claim_preview = ["**Bram** carries **Heartseeker**? _(a player said)_"]
        await DMBot.send_exposure_summary(bot, table)
        text, view = bot.post_message.await_args.args[1:3]
        self.assertNotIn("Recant", text)  # no secret, no Recant part
        self.assertNotIn("Said at the table, now shown", text)
        self.assertIn("🆕 **1 new name to check:** **Bryn Shander**", text)
        self.assertIn("• **Bram** carries **Heartseeker**?", text)
        self.assertIn("…and 1 more in the check.", text)
        self.assertEqual(table.new_names, [])
        self.assertEqual((table.claims_waiting, table.claim_preview), (0, []))
        self.assertEqual(len(view.children), 3)  # the two check buttons and the mark, no Recant

    async def test_with_nothing_to_tell_nothing_is_sent(self) -> None:
        bot = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(bot, self.table())
        bot.post_message.assert_not_awaited()

    async def test_the_words_of_what_was_said_go_only_to_the_dm_never_to_the_screen_post(
        self,
    ) -> None:
        from dmbot.ui.claims_review import waiting_text

        # a screen the players can read gets the count from the "new connections" step
        text = waiting_text(3)
        self.assertNotIn("Bram", text)
        bot = self.bot(campaign(id=self.c, dm_screen_visibility="open"))
        table = self.table()
        table.claims_waiting = 3
        table.claim_preview = ["**Bram** carries **Heartseeker**? _(a player said)_"]
        await DMBot.send_exposure_summary(bot, table)
        bot.post_message.assert_not_awaited()  # nothing on the screen
        self.assertIn("Bram", bot.sidebar_send_dm.await_args.args[1])  # privately, in words
        self.assertEqual(table.claims_waiting, 3)  # the screen is still told the count only

    async def test_when_it_cannot_be_sent_nothing_is_shown_and_it_waits(self) -> None:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c), posted=False)
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertTrue(await self.is_secret(alias))
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 1)

    async def test_when_players_may_look_at_the_screen_each_dm_gets_it_privately(self) -> None:
        alias = await self.waiting_secret()
        camp = campaign(id=self.c, dm_user_ids=frozenset({7, 8}), dm_screen_visibility="open")
        bot = self.bot(camp)
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertEqual(bot.sidebar_send_dm.await_count, 2)
        bot.post_message.assert_not_awaited()  # nothing on the screen the players can read
        self.assertFalse(await self.is_secret(alias))

    async def test_closed_private_messages_show_nothing_and_say_so_without_a_name(self) -> None:
        alias = await self.waiting_secret()
        camp = campaign(id=self.c, dm_screen_visibility="open")
        bot = self.bot(camp, dm="forbidden")
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertTrue(await self.is_secret(alias))
        (call,) = bot.post.await_args_list
        self.assertNotIn("Auril", call.args[1])

    async def test_only_the_cap_is_shown_and_the_rest_wait(self) -> None:
        aliases = []
        for n in range(MAX_LISTED + 2):
            _, alias = await self.secret_name(f"Name{chr(97 + n)}x")
            aliases.append(alias)
        await self.store.record(
            GUILD_A,
            self.c,
            100,
            [
                Exposure("name", spelling_key(f"Name{chr(97 + n)}x"), None, 20 - n)
                for n in range(MAX_LISTED + 2)
            ],
        )
        bot = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertIn("and 2 more", bot.post_message.await_args.args[1])
        self.assertEqual(
            [await self.is_secret(a) for a in aliases], [False] * MAX_LISTED + [True, True]
        )
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)

    async def test_a_summary_pulled_back_from_the_screen_goes_privately_and_counts_once(
        self,
    ) -> None:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        opened = campaign(id=self.c, dm_screen_visibility="open")
        bot.campaigns.get = AsyncMock(side_effect=[campaign(id=self.c), opened])
        message = MESSAGE()
        bot.post_message = AsyncMock(return_value=message)
        await DMBot.send_exposure_summary(bot, self.table())
        message.edit.assert_awaited()  # pulled back: nobody saw the names there
        bot.sidebar_send_dm.assert_awaited_once()  # and the DM got it privately
        self.assertFalse(await self.is_secret(alias))

    async def test_a_summary_pulled_back_with_closed_messages_shows_nothing(self) -> None:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c), dm="forbidden")
        opened = campaign(id=self.c, dm_screen_visibility="open")
        bot.campaigns.get = AsyncMock(side_effect=[campaign(id=self.c), opened])
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertTrue(await self.is_secret(alias))
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 1)

    async def test_a_showing_that_fails_is_tried_again_and_then_the_dm_is_told(self) -> None:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        real = self.memory.reveal_exposed
        calls = {"n": 0}

        async def flaky(*args: Any, **kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("down")
            return await real(*args, **kw)

        with patch.object(self.memory, "reveal_exposed", flaky):
            await DMBot.send_exposure_summary(bot, self.table())
        self.assertEqual(calls["n"], 2)
        self.assertFalse(await self.is_secret(alias))  # the second try worked
        bot.sidebar_send_dm.assert_not_awaited()

        alias2 = await self.waiting_secret_named("Vex")
        bot2 = self.bot(campaign(id=self.c))

        async def broken(*args: Any, **kw: Any) -> Any:
            raise OSError("down")

        with patch.object(self.memory, "reveal_exposed", broken):
            await DMBot.send_exposure_summary(bot2, self.table())
        self.assertTrue(await self.is_secret(alias2))  # still secret
        (call,) = bot2.sidebar_send_dm.await_args_list
        self.assertNotIn("Vex", call.args[1])  # told privately, with no name
        self.assertIn("still secret", call.args[1])
        (row,) = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual(row.spelling, spelling_key("Vex"))  # waits for the next summary

    async def test_a_secret_said_in_two_sessions_is_listed_and_shown_once(self) -> None:
        alias = await self.waiting_secret()
        await self.store.record(
            GUILD_A, self.c, 200, [Exposure("name", spelling_key("Auril"), None, 1)]
        )
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)
        bot = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(bot, self.table())
        text = bot.post_message.await_args.args[1]
        self.assertEqual(text.count("Auril"), 1)
        self.assertFalse(await self.is_secret(alias))
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])  # the other is settled

    async def test_something_no_longer_secret_is_settled_not_told(self) -> None:
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "ghost", None, 1)])
        bot = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(bot, self.table())
        bot.post_message.assert_not_awaited()
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])


class Recanting(StepBase):
    def interaction(self, bot: Any, user: int = DM_ID) -> Any:
        return SimpleNamespace(
            client=bot,
            guild=None,
            user=SimpleNamespace(id=user),
            message=SimpleNamespace(),
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
                is_done=lambda: True,  # (answered at once by the defer: replies go by followup)
            ),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def press(self, bot: Any, exposure_id: int, user: int = DM_ID) -> Any:
        button = RecantButton(exposure_id, self.c, GUILD_A, 1)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="↩️ Recant 1", custom_id=button.item.custom_id))
        interaction = self.interaction(bot, user)
        with patch("discord.ui.View.from_message", return_value=view):
            await button.callback(interaction)
        return interaction

    async def shown(self) -> tuple[Any, str, Any]:
        alias = await self.waiting_secret()
        bot = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(bot, self.table())
        row = await self.store.one(GUILD_A, self.c, 1)
        assert row is not None
        return bot, alias, row

    async def test_the_got_it_button_is_for_the_dm_alone_and_takes_itself_off(self) -> None:
        from dmbot.ui.exposure_summary import SeenButton

        bot = self.bot(campaign(id=self.c))
        button = SeenButton(self.c, GUILD_A)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="👍 Got it", custom_id=button.item.custom_id))
        view.add_item(discord.ui.Button(label="other", custom_id="dmbot:other"))
        stranger = self.interaction(bot, user=99)
        with patch("discord.ui.View.from_message", return_value=view):
            await button.callback(stranger)
        stranger.edit_original_response.assert_not_awaited()
        self.assertEqual(len(view.children), 2)  # a stranger changes nothing
        dm = self.interaction(bot)
        with patch("discord.ui.View.from_message", return_value=view):
            await button.callback(dm)
        self.assertEqual([c.label for c in view.children], ["other"])  # type: ignore[attr-defined]

    async def test_recant_hides_exactly_that_one_again_and_closes_the_button(self) -> None:
        bot, alias, row = await self.shown()
        self.assertFalse(await self.is_secret(alias))
        interaction = await self.press(bot, row.id)
        self.assertTrue(await self.is_secret(alias))
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual(got.status, "recanted")
        sent = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertTrue(sent.children[0].disabled)
        self.assertIn("Taken back", sent.children[0].label)
        self.assertIn("secret again", interaction.followup.send.await_args.args[0])
        self.assertNotIn("Auril", interaction.followup.send.await_args.args[0])

    async def test_a_second_press_and_a_stranger_change_nothing(self) -> None:
        bot, alias, row = await self.shown()
        stranger = await self.press(bot, row.id, user=99)
        self.assertEqual(stranger.followup.send.await_args.args[0], NOT_YOURS)
        self.assertFalse(await self.is_secret(alias))
        await self.press(bot, row.id)
        again = await self.press(bot, row.id)
        self.assertIn("already", again.followup.send.await_args.args[0])
        self.assertTrue(await self.is_secret(alias))

    async def test_an_unknown_exposure_is_not_found(self) -> None:
        bot = self.bot(campaign(id=self.c))
        interaction = await self.press(bot, 987654)
        self.assertEqual(interaction.followup.send.await_args.args[0], GONE)

    async def test_when_it_was_changed_again_since_it_stays_shown_and_says_so(self) -> None:
        bot, alias, row = await self.shown()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET text = 'Auril II' WHERE id = %s", (alias,)
            )
        interaction = await self.press(bot, row.id)
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual(got.status, "applied")  # not marked taken back: it was not
        interaction.followup.send.assert_awaited()

    async def test_a_recant_pressed_before_the_showing_keeps_it_secret(self) -> None:
        alias = await self.waiting_secret()
        row = (await self.store.waiting(GUILD_A, self.c))[0]
        bot = self.bot(campaign(id=self.c))

        async def posted(*args: Any, **kw: Any) -> Any:
            await self.press(bot, row.id)  # the DM presses while the loop has not reached it
            return MESSAGE()

        bot.post_message = posted
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertTrue(await self.is_secret(alias))  # never shown
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual(got.status, "recanted")

    async def test_a_recant_while_it_is_being_shown_waits(self) -> None:
        await self.waiting_secret()
        row = (await self.store.waiting(GUILD_A, self.c))[0]
        await self.store.mark(GUILD_A, self.c, [row.id], "applying")
        interaction = await self.press(self.bot(campaign(id=self.c)), row.id)
        self.assertEqual(interaction.followup.send.await_args.args[0], BUSY)

    async def test_a_recant_before_the_showing_also_settles_the_same_secret_from_earlier(
        self,
    ) -> None:
        alias = await self.waiting_secret()
        await self.store.record(
            GUILD_A, self.c, 50, [Exposure("name", spelling_key("Auril"), None, 1)]
        )  # said in an earlier session too
        first = (await self.store.waiting(GUILD_A, self.c))[0]
        bot = self.bot(campaign(id=self.c))

        async def posted(*args: Any, **kw: Any) -> Any:
            await self.press(bot, first.id)
            return MESSAGE()

        bot.post_message = posted
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertTrue(await self.is_secret(alias))
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])  # nothing left to show
        again = self.bot(campaign(id=self.c))
        await DMBot.send_exposure_summary(again, self.table())
        again.post_message.assert_not_awaited()  # the next summary has nothing to say
        self.assertTrue(await self.is_secret(alias))

    async def test_a_claim_that_died_is_not_busy_forever(self) -> None:
        alias = await self.waiting_secret()
        row = (await self.store.waiting(GUILD_A, self.c))[0]
        await self.store.mark(GUILD_A, self.c, [row.id], "applying")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE secret_exposures SET claimed_at = 1")  # long ago
        interaction = await self.press(self.bot(campaign(id=self.c)), row.id)
        self.assertEqual(interaction.followup.send.await_args.args[0], STUCK)
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual(got.status, "settled")
        self.assertTrue(await self.is_secret(alias))

    async def test_a_recant_on_a_settled_row_says_nothing_was_shown(self) -> None:
        await self.waiting_secret()
        row = (await self.store.waiting(GUILD_A, self.c))[0]
        await self.store.mark(GUILD_A, self.c, [row.id], "settled")
        interaction = await self.press(self.bot(campaign(id=self.c)), row.id)
        self.assertEqual(interaction.followup.send.await_args.args[0], NOTHING)
        interaction.edit_original_response.assert_not_awaited()

    async def test_a_recant_after_the_change_log_undid_it_closes_the_button(self) -> None:
        bot, alias, row = await self.shown()
        assert row.batch is not None
        await self.memory.undo(GUILD_A, self.c, row.batch, source="dm")  # from the change log
        interaction = await self.press(bot, row.id)
        got = await self.store.one(GUILD_A, self.c, row.id)
        assert got is not None
        self.assertEqual(got.status, "recanted")
        interaction.edit_original_response.assert_awaited()
        self.assertTrue(await self.is_secret(alias))


class WhatIsListedIsWhatIsShown(StepBase):
    async def test_a_main_name_made_secret_is_listed_and_shown(self) -> None:
        owner = await self.add("Vexa", type="character", role="npc", status=CONFIRMED)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE entity_id = %s", (owner,)
            )  # a leftover flag on the main name
            cur = await conn.execute("SELECT id FROM memory_aliases WHERE entity_id = %s", (owner,))
            (alias,) = [r["id"] for r in await cur.fetchall()]
        await self.store.record(
            GUILD_A, self.c, 100, [Exposure("name", spelling_key("Vexa"), None, 1)]
        )
        bot: Any = SimpleNamespace(
            memory=self.memory, exposures=self.store, lookup=None,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign(id=self.c))),
            settings=SimpleNamespace(site_url=""), post=AsyncMock(return_value=True),
            post_message=AsyncMock(return_value=MESSAGE()),
            sidebar_send_dm=AsyncMock(return_value="sent"),
        )  # fmt: skip
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertIn("Vexa", bot.post_message.await_args.args[1])
        self.assertFalse(await self.is_secret(alias))

    async def test_a_led_to_link_is_neither_listed_nor_shown(self) -> None:
        a = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        b = await self.add("Rook", type="character", role="npc", status=CONFIRMED)
        written = await self.relate(a, "led_to", b, status=CONFIRMED, secret=True)
        rid = str(written.value[0].id)
        await self.store.record(GUILD_A, self.c, 100, [Exposure("connection", None, rid, 1)])
        rows = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual(await describe(self.memory, GUILD_A, self.c, rows), [])
        shown = await self.memory.reveal_exposed(GUILD_A, self.c, spellings=[], relation_ids=[rid])
        self.assertEqual((shown.value, shown.batch), ((0, 0), None))


class RunningTwice(StepBase):
    async def test_a_second_run_sends_nothing_and_shows_nothing_more(self) -> None:
        await self.secret_name("Auril")
        await self.store.record(
            GUILD_A, self.c, 100, [Exposure("name", spelling_key("Auril"), None, 1)]
        )
        bot: Any = SimpleNamespace(
            memory=self.memory, exposures=self.store, lookup=None,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign(id=self.c))),
            settings=SimpleNamespace(site_url=""), post=AsyncMock(return_value=True),
            post_message=AsyncMock(return_value=MESSAGE()),
            sidebar_send_dm=AsyncMock(return_value="sent"),
        )  # fmt: skip
        await DMBot.send_exposure_summary(bot, self.table())
        await DMBot.send_exposure_summary(bot, self.table())
        self.assertEqual(bot.post_message.await_count, 1)


if __name__ == "__main__":
    unittest.main()
