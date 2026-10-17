"""The rules archive channel (#1752): its name, who sees it (it follows the DM screen's
visibility), that it is read only, that peeking opens it with the DM screen, and that it shows the
saved house rules, edited in place and rebuilt after a restart. A fake Discord; the setup and
isolation tests need the test database."""

from __future__ import annotations

import dataclasses
import unittest
from collections.abc import AsyncIterator
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen import rules_channel as rc
from dmbot.dm_screen.buttons import ConfirmPeek, HideButton
from dmbot.dm_screen.rules import Target
from dmbot.rules.house import HouseRule, HouseRuleError, HouseRuleStore
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import BOT, BOT_PERMS, GENERAL, text_channel

GUILD, OTHER_GUILD, DM, PLAYER = 111, 222, 7, 8
NEW = 680000000000000071  # long: a short id turns up inside timestamps (#1213)
SCREEN = 680000000000000072
ME = BOT


def rule(number: int, text: str = "Crits double the dice", **kw: Any) -> HouseRule:
    fields: dict[str, Any] = {
        "supersedes": None, "scenario": None, "session_id": None, "from_set": None,
    }  # fmt: skip
    fields.update(kw)
    return HouseRule(
        number, "c" * 32, text, fields["supersedes"], fields["scenario"], fields["session_id"],
        DM, 1, 1, 1, fields["from_set"],
    )  # fmt: skip


class Msg:
    def __init__(self, channel: Chan, message_id: int, author: int, content: str) -> None:
        self.channel, self.id, self.author = channel, message_id, MagicMock(id=author)
        self.content, self.pinned = content, False

    async def edit(self, *, content: str, **_: Any) -> None:
        self.channel.edits += 1
        self.content = content

    async def delete(self) -> None:
        self.channel.deletes += 1
        self.channel.messages.remove(self)


class Chan:
    """A channel that remembers what is said in it."""

    def __init__(self) -> None:
        self.messages: list[Msg] = []
        self.sent = self.edits = self.deletes = 0
        self._next = 1000

    def say(self, author: int, content: str) -> Msg:
        self._next += 1
        msg = Msg(self, self._next, author, content)
        self.messages.append(msg)
        return msg

    async def send(self, content: str, **_: Any) -> Msg:
        self.sent += 1
        return self.say(ME, content)

    async def history(
        self, *, limit: int | None = None, oldest_first: bool = False
    ) -> AsyncIterator[Msg]:
        for msg in list(self.messages if oldest_first else reversed(self.messages)):
            yield msg

    def texts(self) -> list[str]:
        return [m.content for m in self.messages if m.author.id == ME]


async def sync(chan: Chan, rules: list[HouseRule], following: list[str] | None = None) -> Any:
    return await rc.sync_rules(chan, ME, rules, following or [])  # type: ignore[arg-type]


class Showing(unittest.IsolatedAsyncioTestCase):
    async def test_one_message_per_saved_rule_and_nothing_again_on_the_next_run(self) -> None:
        chan = Chan()
        done = await sync(chan, [rule(2, "Flanking gives advantage"), rule(1)])
        self.assertEqual((done.sent, done.edited, done.deleted), (2, 0, 0))
        self.assertEqual(
            chan.texts(),
            [
                "🏠 **House rule 1**\nCrits double the dice\n_Added by the DM_",
                "🏠 **House rule 2**\nFlanking gives advantage\n_Added by the DM_",
            ],
        )
        again = await sync(chan, [rule(2, "Flanking gives advantage"), rule(1)])
        self.assertEqual((again.sent, again.edited, again.deleted), (0, 0, 0))
        self.assertEqual(chan.sent, 2)

    async def test_an_edited_rule_is_edited_in_place_not_posted_again(self) -> None:
        chan = Chan()
        await sync(chan, [rule(1)])
        before = chan.messages[0]
        done = await sync(chan, [rule(1, "Crits triple the dice", supersedes="Crits double")])
        self.assertEqual((done.sent, done.edited), (0, 1))
        self.assertIs(chan.messages[0], before)
        self.assertIn("Crits triple the dice", before.content)
        self.assertIn("Replaces: Crits double", before.content)

    async def test_a_removed_rule_is_deleted(self) -> None:
        chan = Chan()
        await sync(chan, [rule(1), rule(2, "Second")])
        done = await sync(chan, [rule(2, "Second")])
        self.assertEqual(done.deleted, 1)
        self.assertEqual(len(chan.texts()), 1)
        self.assertIn("House rule 2", chan.texts()[0])

    async def test_where_each_rule_came_from_is_said(self) -> None:
        self.assertIn("Said at the table, saved by the DM", rc.rule_text(rule(1, session_id="s1")))
        self.assertIn(
            "From the set: Elf house rules, v4",
            rc.rule_text(rule(1, from_set="Elf house rules, v4")),
        )
        self.assertIn("Added by the DM", rc.rule_text(rule(1)))

    async def test_the_story_behind_a_rule_is_never_posted(self) -> None:
        text = rc.rule_text(rule(1, scenario="the party killed the baron's heir"))
        self.assertNotIn("baron", text)  # a DM's own story is not a rule

    async def test_a_followed_set_is_one_line_and_goes_when_it_is_dropped(self) -> None:
        chan = Chan()
        await sync(chan, [], ["Elf house rules"])
        self.assertEqual(
            chan.texts(), ["📚 This campaign follows the house-rule set **Elf house rules**"]
        )
        await sync(chan, [], ["Elf house rules"])
        self.assertEqual(chan.sent, 1)
        await sync(chan, [], [])
        self.assertEqual(chan.texts(), [])

    async def test_at_most_five_edits_a_run_and_the_rest_next_time(self) -> None:
        chan = Chan()
        rules = [rule(n, f"Rule {n}") for n in range(1, 9)]
        await sync(chan, rules)
        changed = [rule(n, f"Rule {n} changed") for n in range(1, 9)]
        first = await sync(chan, changed)
        self.assertEqual((first.edited, first.left_for_next_time), (5, 3))
        second = await sync(chan, changed)
        self.assertEqual((second.edited, second.left_for_next_time), (3, 0))
        self.assertTrue(all("changed" in t for t in chan.texts()))

    async def test_other_peoples_messages_are_never_touched(self) -> None:
        chan = Chan()
        chan.say(999, "🏠 **House rule 1**\nSomeone else's message")
        chan.say(999, "📚 This campaign follows the house-rule set **Fake**")
        await sync(chan, [])
        self.assertEqual(len(chan.messages), 2)
        self.assertEqual((chan.edits, chan.deletes, chan.sent), (0, 0, 0))

    async def test_a_second_copy_of_one_rule_is_cleaned_up(self) -> None:
        chan = Chan()
        text = rc.rule_text(rule(1))
        chan.say(ME, text)
        chan.say(ME, text)
        await sync(chan, [rule(1)])
        self.assertEqual(chan.texts(), [text])

    async def test_a_restart_rebuilds_from_the_channel_without_reposting(self) -> None:
        chan = Chan()
        await sync(chan, [rule(1), rule(2, "Second")])
        sent = chan.sent
        await sync(chan, [rule(1), rule(2, "Second")])  # (a new process reads the same channel)
        self.assertEqual(chan.sent, sent)
        fresh = Chan()  # the channel was emptied by hand
        await sync(fresh, [rule(1), rule(2, "Second")])
        self.assertEqual(len(fresh.texts()), 2)


class Who(unittest.TestCase):
    def plan(self, visibility: str, peekers: tuple[int, ...] = ()) -> Any:
        return rc.rules_plan(
            visibility, guild_id=GUILD, bot_id=BOT, dm_ids=[DM], peeker_ids=peekers
        )

    def test_private_shows_it_to_the_dm_only_and_nobody_posts(self) -> None:
        plan = self.plan("private")
        self.assertEqual(plan[Target("role", GUILD)], {"view_channel": False})
        dm = plan[Target("member", DM)]
        self.assertTrue(dm["view_channel"])
        self.assertFalse(dm["send_messages"])  # even the DM does not post here
        self.assertTrue(plan[Target("member", BOT)]["send_messages"])

    def test_open_shows_it_to_everyone_read_only(self) -> None:
        everyone = self.plan("open")[Target("role", GUILD)]
        self.assertTrue(everyone["view_channel"] and everyone["read_message_history"])
        for denied in (
            "send_messages", "send_messages_in_threads", "create_public_threads",
            "create_private_threads", "add_reactions",
        ):  # fmt: skip
            self.assertFalse(everyone[denied], denied)
        self.assertNotIn("use_application_commands", everyone)  # slash commands are not blocked

    def test_peek_shows_it_to_the_players_who_peeked_at_the_screen(self) -> None:
        plan = self.plan("peek", (PLAYER,))
        self.assertEqual(plan[Target("role", GUILD)], {"view_channel": False})
        self.assertTrue(plan[Target("member", PLAYER)]["view_channel"])
        self.assertFalse(plan[Target("member", PLAYER)]["send_messages"])

    def test_switching_away_from_peek_drops_the_peekers(self) -> None:
        self.assertNotIn(Target("member", PLAYER), self.plan("private", (PLAYER,)))
        self.assertNotIn(Target("member", PLAYER), self.plan("open", (PLAYER,)))

    def test_the_warning_names_both_channels(self) -> None:
        text = messages.peek_warning("<#99>")
        self.assertIn("DM screen", text)
        self.assertIn("<#99>", text)
        self.assertEqual(messages.peek_warning(None), messages.PEEK_WARNING)  # no rules channel


class Fake(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.houses = HouseRuleStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime of the Frostmaiden", DM)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-rules-rmfthfrstmdn", guild)
        self.new.pins = AsyncMock(return_value=[])
        self.screen = text_channel(SCREEN, "dmb-dm-screen-rime-of-the-frostmaiden", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: {NEW: self.new, SCREEN: self.screen}.get(cid)
        self.guild = guild

    def created(self) -> dict[Any, discord.PermissionOverwrite]:
        return self.guild.create_text_channel.call_args.kwargs["overwrites"]  # type: ignore[no-any-return]


class Setup(Fake):
    async def test_a_new_channel_is_named_saved_with_a_topic_and_a_pinned_card(self) -> None:
        channel = await rc.setup_rules_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        call = self.guild.create_text_channel.call_args
        self.assertEqual(call.args[0], "dmb-rules-rmfthfrstmdn")
        self.assertEqual(call.kwargs["topic"], messages.RULES_TOPIC)
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.rules_channel_id == NEW
        self.assertIsNone(saved.dmbchat_channel_id)  # each channel keeps its own id
        send: Any = self.new.send
        card = send.call_args.args[0]
        self.assertEqual(
            card,
            "📖 **What's this channel?**\n"
            "The house rules for **Rime of the Frostmaiden**, saved by your DM. "
            "Only DMbot posts here, so you can read but not write. "
            "Your DM decides what applies. If a rule isn't clear, ask your DM.",
        )

    async def test_the_name_follows_the_clash_number(self) -> None:
        self.assertEqual(rc.rules_channel_name(self.campaign), "dmb-rules-rmfthfrstmdn")
        second = dataclasses.replace(self.campaign, channel_number=2)
        self.assertEqual(rc.rules_channel_name(second), "dmb-rules-2rmfthfrstmdn")
        self.assertTrue(rc.is_rules_name("dmb-rules-x"))
        self.assertFalse(rc.is_rules_name("dmb-chat-x"))
        self.assertFalse(rc.is_rules_name("general"))

    async def test_it_follows_the_visibility_when_made(self) -> None:
        for visibility, seen in (("private", False), ("open", True)):
            await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, visibility)
            self.guild.create_text_channel.reset_mock()
            await self.store.set_rules_channel(GUILD, self.campaign.id, None)
            await rc.setup_rules_channel(self.guild, self.campaign.id, self.store)
            everyone = next(ow for key, ow in self.created().items() if key.id == GUILD)
            self.assertEqual(bool(everyone.view_channel), seen, visibility)
            self.assertFalse(everyone.send_messages)
            self.assertIsNot(everyone.use_application_commands, False)

    async def test_peekers_of_the_screen_see_it_in_peek_mode(self) -> None:
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "peek")
        peeker = MagicMock(spec=discord.Member, id=PLAYER)
        self.screen.overwrites = {
            peeker: discord.PermissionOverwrite(view_channel=True, send_messages=False)
        }
        await rc.setup_rules_channel(self.guild, self.campaign.id, self.store, screen=self.screen)
        seen = next(ow for key, ow in self.created().items() if key.id == PLAYER)
        self.assertTrue(seen.view_channel)
        self.assertFalse(seen.send_messages)

    async def test_a_channel_that_is_not_a_rules_one_is_never_edited(self) -> None:
        await self.store.set_rules_channel(GUILD, self.campaign.id, GENERAL)
        await rc.setup_rules_channel(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_awaited()
        self.guild.create_text_channel.assert_awaited_once()

    async def test_a_channel_deleted_by_hand_is_made_again_at_the_next_start(self) -> None:
        await self.store.set_rules_channel(GUILD, self.campaign.id, 12345)
        self.guild.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
        await rc.setup_rules_channel(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_awaited_once()
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.rules_channel_id == NEW

    async def test_the_screens_change_reaches_an_existing_rules_channel(self) -> None:
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "private")
        await self.store.set_rules_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        self.new.topic = messages.RULES_TOPIC
        self.new.overwrites = {}
        campaign = await self.store.get(GUILD, self.campaign.id)
        assert campaign is not None
        await rc.follow_screen(self.guild, campaign, self.screen)
        self.new.edit.assert_awaited()
        sent = self.new.edit.call_args.kwargs["overwrites"]
        everyone = next(ow for key, ow in sent.items() if key.id == GUILD)
        self.assertFalse(everyone.view_channel)

    async def test_nothing_happens_for_a_campaign_with_no_rules_channel(self) -> None:
        await rc.follow_screen(self.guild, self.campaign, self.screen)
        self.new.edit.assert_not_awaited()

    async def test_a_backup_never_holds_the_channel_id(self) -> None:
        await self.store.set_rules_channel(GUILD, self.campaign.id, NEW)
        exported = await self.store.export(GUILD, self.campaign.id)
        self.assertNotIn(str(NEW), repr(exported))
        self.assertNotIn("rules_channel", repr(exported))

    async def test_it_is_never_picked_as_the_dms_extra_table_chat(self) -> None:
        await self.store.set_rules_channel(GUILD, self.campaign.id, NEW)
        self.assertIn(NEW, await self.store.special_channel_ids(GUILD))


class FromTheStores(Fake):
    """The rules come from the campaign's saved rules and nobody else's."""

    async def test_a_campaigns_channel_shows_its_own_rules_only(self) -> None:
        mine = await self.store.set_rules_channel(GUILD, self.campaign.id, NEW)
        other = await self.store.create(GUILD, "Other table", DM)
        faraway = await self.store.create(OTHER_GUILD, "Far away", DM)
        await self.houses.add(GUILD, mine.id, DM, "Crits double the dice")
        await self.houses.add(GUILD, other.id, DM, "Healing potions are a bonus action")
        await self.houses.add(OTHER_GUILD, faraway.id, DM, "No flanking")
        chan = Chan()
        shown = text_channel(NEW, "dmb-rules-rmfthfrstmdn", self.guild)
        shown.history, shown.send = chan.history, chan.send
        self.guild.fetch_channel = AsyncMock(return_value=shown)
        done = await rc.sync_campaign(self.guild, mine, self.houses, None)
        assert done is not None
        self.assertEqual(done.sent, 1)
        self.assertEqual(len(chan.texts()), 1)
        self.assertIn("Crits double the dice", chan.texts()[0])
        self.assertNotIn("bonus action", " ".join(chan.texts()))
        self.assertNotIn("flanking", " ".join(chan.texts()).lower())

    async def test_a_campaign_without_a_channel_is_left_alone(self) -> None:
        self.assertIsNone(await rc.sync_campaign(self.guild, self.campaign, self.houses, None))

    async def test_a_change_to_the_stores_is_told_to_the_hook(self) -> None:
        told: list[tuple[int, str]] = []
        self.houses.on_change = lambda g, c: told.append((g, c))
        await self.houses.add(GUILD, self.campaign.id, DM, "Crits double the dice")
        await self.houses.edit(GUILD, self.campaign.id, DM, 1, "Crits triple the dice")
        await self.houses.remove(GUILD, self.campaign.id, DM, 1)
        self.assertEqual(told, [(GUILD, self.campaign.id)] * 3)

    async def test_a_refused_change_is_not_told(self) -> None:
        told: list[tuple[int, str]] = []
        self.houses.on_change = lambda g, c: told.append((g, c))
        with self.assertRaises(HouseRuleError):
            await self.houses.add(GUILD, self.campaign.id, PLAYER, "Not a DM's rule")
        self.assertEqual(told, [])

    async def test_a_hook_that_fails_does_not_undo_a_saved_rule(self) -> None:
        def boom(_g: int, _c: str) -> None:
            raise RuntimeError("hook down")

        self.houses.on_change = boom
        saved = await self.houses.add(GUILD, self.campaign.id, DM, "Crits double the dice")
        self.assertEqual(saved.number, 1)
        self.assertEqual(len(await self.houses.list(GUILD, self.campaign.id)), 1)


class Peeking(Fake):
    """A peek opens the DM screen and the rules channel together, a hide closes both."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.set_dm_screen(GUILD, self.campaign.id, SCREEN)
        await self.store.set_rules_channel(GUILD, self.campaign.id, NEW)
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "peek")
        self.guild.fetch_channel = AsyncMock(
            side_effect=lambda cid: {NEW: self.new, SCREEN: self.screen}[cid]
        )
        self.screen.set_permissions = AsyncMock()
        self.new.set_permissions = AsyncMock()
        self.screen.send = AsyncMock()
        self.member = MagicMock(spec=discord.Member)
        self.member.id = PLAYER
        self.member.display_name = "Pip"
        self.interaction = MagicMock()
        self.interaction.guild = self.guild
        self.interaction.user = self.member
        self.interaction.client.campaigns = self.store
        self.interaction.response.edit_message = AsyncMock()
        self.interaction.response.send_message = AsyncMock()

    async def test_yes_grants_the_peek_in_both_channels(self) -> None:
        view = ConfirmPeek(self.campaign.id, self.interaction)
        await view.yes.callback(self.interaction)
        self.screen.set_permissions.assert_awaited_once()
        self.new.set_permissions.assert_awaited_once()
        granted = self.new.set_permissions.call_args.kwargs["overwrite"]
        self.assertTrue(granted.view_channel)
        self.assertFalse(granted.send_messages)

    async def test_a_failure_in_the_rules_channel_does_not_undo_the_screen_peek(self) -> None:
        self.new.set_permissions = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "down")
        )
        view = ConfirmPeek(self.campaign.id, self.interaction)
        await view.yes.callback(self.interaction)
        self.screen.set_permissions.assert_awaited_once()
        text = self.interaction.response.edit_message.call_args.kwargs["content"]
        self.assertEqual(text, messages.peek_done(self.screen.mention))

    async def test_hide_closes_both(self) -> None:
        overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        self.screen.overwrites = {self.member: overwrite}
        self.guild.get_member = lambda _id: self.member
        await HideButton(self.campaign.id).callback(self.interaction)
        self.screen.set_permissions.assert_awaited_once()
        self.new.set_permissions.assert_awaited_once()
        self.assertIsNone(self.new.set_permissions.call_args.kwargs["overwrite"])


class Migration0127(DatabaseTest):
    async def test_rows_that_exist_are_unchanged_and_the_new_id_is_empty(self) -> None:
        from dmbot.db import Database, drop_schema
        from dmbot.schema import MIGRATIONS
        from tests.pg import TEST_URL

        before = [m for m in MIGRATIONS if m[0] < "0127"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        made = await campaigns.create(GUILD, "Frozen Wastes", DM)
        await self.db.migrate()
        campaign = await campaigns.get(GUILD, made.id)
        assert campaign is not None
        self.assertEqual(campaign.name, "Frozen Wastes")
        self.assertIsNone(campaign.rules_channel_id)
        await campaigns.set_rules_channel(GUILD, made.id, NEW)
        again = await campaigns.get(GUILD, made.id)
        assert again is not None and again.rules_channel_id == NEW


class Keeping(unittest.IsolatedAsyncioTestCase):
    """Work over the caps or refused by Discord is finished on later passes, never lost."""

    async def test_many_stale_messages_are_cleared_over_several_runs(self) -> None:
        chan = Chan()
        for n in range(1, 31):
            chan.say(ME, rc.rule_text(rule(n, f"Old {n}")))
        runs = 0
        while chan.texts() and runs < 20:
            done = await sync(chan, [])
            runs += 1
            self.assertLessEqual(done.deleted, rc.MAX_EDITS)
        self.assertEqual(chan.texts(), [])
        self.assertEqual(runs, 6)  # 30 messages at 5 a run

    async def test_many_new_rules_are_posted_over_several_runs(self) -> None:
        chan = Chan()
        rules = [rule(n, f"Rule {n}") for n in range(1, 61)]
        first = await sync(chan, rules)
        self.assertEqual((first.sent, first.left_for_next_time), (rc.MAX_SENDS, 35))
        while (await sync(chan, rules)).left_for_next_time:
            pass
        self.assertEqual(len(chan.texts()), 60)

    async def test_a_message_discord_refuses_is_counted_and_the_rest_still_done(self) -> None:
        chan = Chan()
        await sync(chan, [rule(1), rule(2, "Second")])
        refuse = discord.HTTPException(MagicMock(status=403), "Forbidden")
        chan.messages[0].edit = AsyncMock(side_effect=refuse)  # type: ignore[method-assign]
        done = await sync(chan, [rule(1, "Changed"), rule(2, "Second changed")])
        self.assertEqual((done.edited, done.left_for_next_time), (1, 1))
        self.assertIn("Second changed", chan.texts()[1])

    async def test_a_post_that_fails_does_not_stop_the_next_ones(self) -> None:
        chan = Chan()
        real = chan.send
        calls = 0

        async def flaky(content: str, **kw: Any) -> Msg:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise discord.HTTPException(MagicMock(status=500), "down")
            return await real(content, **kw)

        chan.send = flaky  # type: ignore[method-assign]
        done = await sync(chan, [rule(1), rule(2, "Second")])
        self.assertEqual((done.sent, done.left_for_next_time), (1, 1))

    async def test_a_very_long_rule_never_goes_over_the_message_limit(self) -> None:
        text = rc.rule_text(rule(1, "x" * 500, supersedes="y" * 500, from_set="z" * 200))
        self.assertLessEqual(len(text), 2000)

    async def test_a_mention_in_a_rule_pings_nobody(self) -> None:
        chan = Chan()
        seen: list[Any] = []
        real = chan.send

        async def watching(content: str, **kw: Any) -> Msg:
            seen.append(kw.get("allowed_mentions"))
            return await real(content, **kw)

        chan.send = watching  # type: ignore[method-assign]
        await sync(chan, [rule(1, "@everyone roll twice")])
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].everyone or seen[0].users or seen[0].roles)

    async def test_an_unknown_or_repeated_following_line_is_cleaned_up(self) -> None:
        chan = Chan()
        chan.say(ME, rc.follow_text("Old set"))
        chan.say(ME, rc.follow_text("Elf rules"))
        chan.say(ME, rc.follow_text("Elf rules"))
        await sync(chan, [], ["Elf rules"])
        self.assertEqual(chan.texts(), [rc.follow_text("Elf rules")])

    async def test_a_message_that_only_looks_like_a_rule_is_not_one(self) -> None:
        self.assertIsNone(rc._rule_number("🏠 **House rule x**\nwords"))
        self.assertEqual(rc._rule_number("🏠 **House rule 12**\nwords"), 12)
        self.assertIsNone(rc._rule_number("hello"))


class OnTheBot(unittest.IsolatedAsyncioTestCase):
    """The bot's side: changes gathered into one pass, and a second pass when more came in."""

    def bot(self) -> Any:
        from types import SimpleNamespace

        bot = SimpleNamespace(
            _rules_sync={}, _rules_dirty=set(), passes=[], _closing=False,
        )  # fmt: skip

        def track(work: Any, name: str) -> Any:
            import asyncio

            return asyncio.ensure_future(work)

        async def sync_once(guild_id: int, campaign_id: str) -> Any:
            bot.passes.append((guild_id, campaign_id))
            return SimpleNamespace(left_for_next_time=bot.left.pop(0) if bot.left else 0)

        bot._track = track
        bot._sync_rules_archive = sync_once
        bot.left = []
        return bot

    async def run_it(self, bot: Any) -> None:
        import asyncio

        from dmbot import bot as bot_module

        with (
            mock.patch.object(bot_module, "RULES_ARCHIVE_DELAY_S", 0.0),
            mock.patch.object(bot_module, "RULES_ARCHIVE_RETRY_S", 0.0),
        ):
            for _ in range(30):
                await asyncio.sleep(0)

    async def test_ten_quick_changes_make_one_pass(self) -> None:
        from dmbot.bot import DMBot

        bot = self.bot()
        from dmbot import bot as bot_module

        with (
            mock.patch.object(bot_module, "RULES_ARCHIVE_DELAY_S", 0.0),
            mock.patch.object(bot_module, "RULES_ARCHIVE_RETRY_S", 0.0),
        ):
            bot._rules_archive_run = lambda key: DMBot._rules_archive_run(bot, key)
            for _ in range(10):
                DMBot._rules_archive_changed(bot, GUILD, "c" * 32)
            await self.run_it(bot)
        self.assertEqual(bot.passes, [(GUILD, "c" * 32)])
        self.assertEqual(bot._rules_sync, {})  # nothing is left behind

    async def test_a_change_during_a_pass_makes_exactly_one_more(self) -> None:
        import asyncio

        from dmbot import bot as bot_module
        from dmbot.bot import DMBot

        bot = self.bot()
        key = (GUILD, "c" * 32)
        original = bot._sync_rules_archive

        async def changing(guild_id: int, campaign_id: str) -> Any:
            done = await original(guild_id, campaign_id)
            if len(bot.passes) == 1:
                DMBot._rules_archive_changed(bot, *key)  # a change lands while it runs
            return done

        bot._sync_rules_archive = changing
        bot._rules_archive_run = lambda k: DMBot._rules_archive_run(bot, k)
        with (
            mock.patch.object(bot_module, "RULES_ARCHIVE_DELAY_S", 0.0),
            mock.patch.object(bot_module, "RULES_ARCHIVE_RETRY_S", 0.0),
        ):
            DMBot._rules_archive_changed(bot, *key)
            for _ in range(40):
                await asyncio.sleep(0)
        self.assertEqual(len(bot.passes), 2)
        self.assertEqual(bot._rules_sync, {})

    async def test_work_left_over_is_run_again_until_it_is_done(self) -> None:
        import asyncio

        from dmbot import bot as bot_module
        from dmbot.bot import DMBot

        bot = self.bot()
        bot.left = [5, 3, 0]
        bot._rules_archive_run = lambda k: DMBot._rules_archive_run(bot, k)
        with (
            mock.patch.object(bot_module, "RULES_ARCHIVE_DELAY_S", 0.0),
            mock.patch.object(bot_module, "RULES_ARCHIVE_RETRY_S", 0.0),
        ):
            DMBot._rules_archive_changed(bot, GUILD, "c" * 32)
            for _ in range(40):
                await asyncio.sleep(0)
        self.assertEqual(len(bot.passes), 3)

    async def test_a_failing_pass_is_tried_again_and_still_cleans_up(self) -> None:
        import asyncio

        from dmbot import bot as bot_module
        from dmbot.bot import DMBot

        bot = self.bot()
        tries = 0

        async def failing_once(guild_id: int, campaign_id: str) -> Any:
            nonlocal tries
            tries += 1
            if tries == 1:
                raise RuntimeError("Discord is down")
            return None

        bot._sync_rules_archive = failing_once
        bot._rules_archive_run = lambda k: DMBot._rules_archive_run(bot, k)
        with (
            mock.patch.object(bot_module, "RULES_ARCHIVE_DELAY_S", 0.0),
            mock.patch.object(bot_module, "RULES_ARCHIVE_RETRY_S", 0.0),
        ):
            DMBot._rules_archive_changed(bot, GUILD, "c" * 32)
            for _ in range(40):
                await asyncio.sleep(0)
        self.assertEqual(tries, 2)
        self.assertEqual((bot._rules_sync, bot._rules_dirty), ({}, set()))

    async def test_nothing_is_started_while_the_bot_is_closing(self) -> None:
        import asyncio

        from dmbot.bot import DMBot

        bot = self.bot()
        bot._track = lambda work, name: (work.close(), None)[1]
        bot._rules_archive_run = lambda k: DMBot._rules_archive_run(bot, k)
        DMBot._rules_archive_changed(bot, GUILD, "c" * 32)
        await asyncio.sleep(0)
        self.assertEqual((bot.passes, bot._rules_sync), ([], {}))


class FollowedSetsHook(DatabaseTest):
    async def test_following_and_unfollowing_a_set_tells_the_hook(self) -> None:
        from dmbot.rules.rule_sets import RuleSetStore

        campaigns = CampaignStore(self.db)
        mine = await campaigns.create(GUILD, "Far Away", DM)
        pub = await campaigns.create(GUILD, "Frostmaiden", DM)
        houses = HouseRuleStore(self.db)
        sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        await houses.add(GUILD, pub.id, DM, "Crits double the dice")
        await sets.publish(GUILD, pub.id, DM, "Frostmaiden", await houses.list(GUILD, pub.id), None)
        code = await sets.make_code(GUILD, pub.id, DM)
        told: list[tuple[int, str]] = []
        sets.on_change = lambda g, c: told.append((g, c))
        offer = await sets.by_code(GUILD, DM, code)
        follow = await sets.follow(GUILD, mine.id, DM, offer)
        await sets.unfollow(GUILD, mine.id, DM)
        await sets.restore(GUILD, mine.id, DM, follow)
        self.assertEqual(told, [(GUILD, mine.id)] * 3)

    async def test_a_refused_follow_is_not_told(self) -> None:
        from dmbot.rules.rule_sets import RuleSetStore

        campaigns = CampaignStore(self.db)
        mine = await campaigns.create(GUILD, "Far Away", DM)
        sets = RuleSetStore(self.db)
        told: list[tuple[int, str]] = []
        sets.on_change = lambda g, c: told.append((g, c))
        with self.assertRaises(Exception):  # noqa: B017  (any refusal: nothing is followed)
            await sets.unfollow(GUILD, mine.id, DM)  # nothing to stop following
        self.assertEqual(told, [])
