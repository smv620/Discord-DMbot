"""Table questions (#1187), the parts that need no database: the answer engine's table mode, the
link allowlist and the bot's hook for messages typed in the DMbot chat."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import context, table_links
from dmbot.sidebar.answer import ONE_FOR_DM, Sidebar
from tests.sidebar_brevity_cases import reply
from tests.test_memory_lookup import data
from tests.test_sidebar_answer import FakeAI, campaign
from tests.test_sidebar_bot import DM, GUILD, PLAYER, make_bot, make_table

CHAT = 555


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def engine(ai: Any) -> Sidebar:
    async def gate(c: Campaign, user: int) -> str | None:
        return None

    async def houses(c: Campaign) -> Sequence[HouseRule]:
        return []

    async def names(c: Campaign) -> CampaignLookup:
        return CampaignLookup.build(data())  # has a secret alias: "the hooded stranger"

    return Sidebar(ai, srd(), gate=gate, houses=houses, names=names)


class TheTablePrompt(unittest.TestCase):
    def test_a_table_question_says_who_reads_it_and_a_private_one_does_not(self) -> None:
        ai = FakeAI([reply("Yes.", "none"), reply("Yes.", "none")])
        run(
            engine(ai).answer(campaign(), "does grappling need a free hand", asker_id=1, table=True)
        )
        run(engine(ai).answer(campaign(), "does grappling need a free hand", asker_id=1))
        self.assertTrue(ai.prompts[0].startswith("AUDIENCE: THE TABLE"))
        self.assertNotIn("AUDIENCE", ai.prompts[1])
        self.assertIn("AUDIENCE: THE TABLE", ai.systems[0])  # the rule that explains it

    def test_the_campaigns_names_are_not_even_read_for_a_table_question(self) -> None:
        question = "who is Belleros, the hooded stranger"
        ai = FakeAI([reply("Yes.", "none"), reply("Yes.", "none")])
        run(engine(ai).answer(campaign(), question, asker_id=1, table=True))
        run(engine(ai).answer(campaign(), question, asker_id=1))
        # (the question names him, so the name itself is in the prompt as the question)
        self.assertNotIn("CAMPAIGN NAMES", ai.prompts[0])
        self.assertIn("CAMPAIGN NAMES", ai.prompts[1])  # the DM's own chat still has them

    def test_nothing_from_the_dm_screen_is_in_a_table_prompt(self) -> None:
        built = context.build(
            "who is Belleros",
            target="2024",
            fallback="2014",
            index=srd(),
            house_rules=[],
            lookup=None,
            scene="",
            table=True,
        )
        self.assertNotIn("CAMPAIGN NAMES", built.prompt)

    def test_the_fixed_line_for_a_question_that_needs_the_dm(self) -> None:
        ai = FakeAI([reply("That's one for your DM.", "none")])
        got = run(engine(ai).answer(campaign(), "what is behind the door", asker_id=1, table=True))
        self.assertEqual((got.text, got.one_for_dm), (ONE_FOR_DM, True))
        self.assertNotIn("(", got.text)  # no source, no "sure"

    def test_a_players_trap_question_gets_the_line_for_the_dm_and_the_trap_stays_unsent(
        self,
    ) -> None:
        # The Gameplay Toolbox is the DM's chapter (#1935): a trap's detection DCs would spoil it.
        question = "how do I disarm the pit trap"
        ai = FakeAI([reply("That's one for your DM.", "none"), reply("DC 15.", "SRD 5.2.1 p. 200")])
        table = run(engine(ai).answer(campaign(), question, asker_id=1, table=True))
        self.assertEqual((table.text, table.one_for_dm), (ONE_FOR_DM, True))
        self.assertNotIn("Hidden Pit", ai.prompts[0])
        self.assertNotIn("DC 15 Intelligence (Investigation)", ai.prompts[0])
        self.assertNotIn("Trigger", ai.prompts[0])
        # The DM's own chat gets the trap, with its DCs.
        run(engine(ai).answer(campaign(), question, asker_id=1))
        self.assertIn("Hidden Pit (trap)", ai.prompts[1])
        self.assertIn("DC 15 Intelligence (Investigation) check", ai.prompts[1])

    def test_that_line_means_nothing_in_the_dms_private_chat(self) -> None:
        ai = FakeAI([reply("That's one for your DM.", "none", "uncertain")])
        got = run(engine(ai).answer(campaign(), "what is behind the door", asker_id=1))
        self.assertFalse(got.one_for_dm)

    def test_a_table_answer_about_dmbot_never_carries_the_github_link(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        ai = FakeAI(
            [
                reply("It keeps the game's time.", "DMbot help", in_game="no"),
                reply("It keeps the game's time.", "DMbot help", in_game="no"),
            ]
        )
        with patch.dict("os.environ", {"GIT_COMMIT": commit}):
            private = run(engine(ai).answer(campaign(), "what does the game clock do", asker_id=1))
            table = run(
                engine(ai).answer(campaign(), "what does the game clock do", asker_id=1, table=True)
            )
        self.assertIn("More: https://github.com", private.text)
        self.assertNotIn("http", table.text)


class TheRestatedQuestion(unittest.TestCase):
    """The owner asked for the Q line to be paraphrased: a few words from the same AI call."""

    def test_the_reply_form_has_a_question_field_read_and_cut_to_twelve_words(self) -> None:
        from dmbot.sidebar.answer import RESTATED_WORDS, parse

        fields = parse(
            "ANSWER: No.\nSOURCE: none\nCONFIDENCE: confident\nIN_GAME: yes\nON_TOPIC: yes\n"
            "QUESTION: Does fireball need line of sight to the target or only a point within range"
            " that you can see at all?"
        )
        self.assertEqual(len(fields.question.split()), RESTATED_WORDS)
        self.assertTrue(fields.question.startswith("Does fireball need line of sight"))

    def test_none_and_links_are_not_a_question(self) -> None:
        from dmbot.sidebar.answer import parse

        self.assertEqual(parse("ANSWER: Yes.\nQUESTION: none").question, "")
        self.assertEqual(
            parse("ANSWER: Yes.\nQUESTION: is https://evil.example.net/x fine to use").question,
            "is fine to use",
        )
        self.assertEqual(parse("ANSWER: Yes.").question, "")  # an old-form reply still parses

    def test_a_table_answer_carries_it_and_a_private_one_does_not(self) -> None:
        raw = (
            "ANSWER: No.\nSOURCE: none\nCONFIDENCE: confident\nIN_GAME: yes\nON_TOPIC: yes\n"
            "QUESTION: Fireball needs a clear line?"
        )
        ai = FakeAI([raw, raw])
        table = run(
            engine(ai).answer(campaign(), "fireball and line of sight", asker_id=1, table=True)
        )
        private = run(engine(ai).answer(campaign(), "fireball and line of sight", asker_id=1))
        self.assertEqual(table.question, "Fireball needs a clear line?")
        self.assertEqual(private.question, "")

    def test_the_prompt_asks_for_it_and_the_version_moved_on(self) -> None:
        from dmbot.sidebar import answer

        self.assertIn("QUESTION:", answer.SYSTEM)
        self.assertEqual(answer.PROMPT_VERSION, "sidebar-13")


class LinkAllowlist(unittest.TestCase):
    HOUSE = "https://docs.example.org/our-house-rules"

    def test_dndbeyond_and_this_campaigns_link_are_kept(self) -> None:
        self.assertEqual(
            table_links.keep_allowed("See https://www.dndbeyond.com/sources/x.", []),
            "See https://www.dndbeyond.com/sources/x.",
        )
        self.assertEqual(
            table_links.keep_allowed(f"Yes {self.HOUSE}/", [self.HOUSE]), f"Yes {self.HOUSE}/"
        )

    def test_any_other_link_is_dropped(self) -> None:
        got = table_links.keep_allowed("Yes https://evil.example.net/x here", [self.HOUSE])
        self.assertEqual(got, "Yes here")
        # a look-alike of the official site is not the official site
        self.assertEqual(
            table_links.keep_allowed("https://dndbeyond.com.evil.example.net/x", []), ""
        )
        self.assertEqual(table_links.keep_allowed("https://notdndbeyond.com/x", []), "")

    def test_another_campaigns_link_is_not_on_this_campaigns_list(self) -> None:
        other = "https://docs.example.org/another-campaign"
        self.assertEqual(table_links.keep_allowed(f"Yes {other}", [self.HOUSE]), "Yes")

    def test_a_backslash_or_user_info_cannot_pass_as_dndbeyond(self) -> None:
        for sneaky in (
            "https://evil.example.net\\@dndbeyond.com/x",
            "https://user@dndbeyond.com/x",
            "http://www.dndbeyond.com/x",  # (not https)
            "https://www.dndbeyond.com:8443/x",
        ):
            self.assertEqual(table_links.keep_allowed(f"Yes {sneaky}", []), "Yes", sneaky)

    def test_bare_addresses_are_dropped_too(self) -> None:
        for bare in ("discord.gg/abc", "www.evil.example", "evil.com/path"):
            self.assertNotIn(bare, table_links.keep_allowed(f"Yes, see {bare} now", []))
        self.assertEqual(
            table_links.keep_allowed("The Bag of Holding holds 500 lb. Yes.", []),
            "The Bag of Holding holds 500 lb. Yes.",
        )

    def test_one_link_across_all_the_parts_of_a_message(self) -> None:
        got = table_links.keep_allowed_in(
            ["https://www.dndbeyond.com/a", "and https://www.dndbeyond.com/b"], []
        )
        self.assertEqual(sum(x.count("http") for x in got), 1)

    def test_a_link_in_what_a_person_typed_is_taken_out(self) -> None:
        self.assertEqual(
            table_links.strip_links("is https://evil.example.net/x ok? or discord.gg/abc"),
            "is ok? or",
        )

    def test_never_more_than_one_link(self) -> None:
        got = table_links.keep_allowed(
            "https://www.dndbeyond.com/a and https://www.dndbeyond.com/b", []
        )
        self.assertEqual(got.count("http"), 1)


def message(
    text: str = "does fireball need line of sight",
    *,
    channel: int = CHAT,
    author: int = PLAYER,
    bot: bool = False,
    webhook: int | None = None,
) -> Any:
    return SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        channel=SimpleNamespace(id=channel),
        author=SimpleNamespace(id=author, bot=bot),
        webhook_id=webhook,
        type=discord.MessageType.default,
        clean_content=text,
        interaction_metadata=None,
    )


class TheChatHook(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = make_bot()
        self.table = make_table()
        self.table.dmbchat_channel_id = CHAT
        self.table.listening = True
        self.bot.tables[GUILD] = self.table
        self.asked = MagicMock()
        self.bot.sidebar.ask_in_chat = self.asked  # type: ignore[method-assign]

    def test_a_message_there_is_a_question_from_its_author(self) -> None:
        self.assertTrue(self.bot._hear_dmbchat(message()))
        self.asked.assert_called_once_with(self.table, PLAYER, "does fireball need line of sight")

    def test_bots_webhooks_and_dmbot_itself_are_never_answered(self) -> None:
        cast(Any, self.bot._connection).user = SimpleNamespace(id=999)
        for msg in (message(bot=True), message(webhook=5), message(author=999)):
            self.assertTrue(self.bot._hear_dmbchat(msg))  # (handled: nothing else reads it)
        self.asked.assert_not_called()

    def test_not_while_dmbot_is_not_listening(self) -> None:
        self.table.listening = False
        self.assertTrue(self.bot._hear_dmbchat(message()))
        self.asked.assert_not_called()

    def test_only_this_campaigns_chat_counts(self) -> None:
        self.assertFalse(self.bot._hear_dmbchat(message(channel=556)))  # another campaign's
        self.asked.assert_not_called()

    def test_without_a_session_nothing_is_read(self) -> None:
        self.bot.tables.clear()
        self.assertFalse(self.bot._hear_dmbchat(message()))
        self.asked.assert_not_called()


class TheChatThroughOnMessage(unittest.IsolatedAsyncioTestCase):
    async def test_a_chat_message_is_not_also_read_as_table_chat(self) -> None:
        bot = make_bot()
        table = make_table()
        table.dmbchat_channel_id = CHAT
        table.listening = True
        bot.tables[GUILD] = table
        asked, typed = MagicMock(), MagicMock()
        bot.sidebar.ask_in_chat = asked  # type: ignore[method-assign]
        bot._hear_typed = typed  # type: ignore[method-assign]
        await bot.on_message(message())
        asked.assert_called_once()
        typed.assert_not_called()

    async def test_the_dm_asking_there_is_a_question_like_anyone(self) -> None:
        bot = make_bot()
        table = make_table()
        table.dmbchat_channel_id = CHAT
        table.listening = True
        bot.tables[GUILD] = table
        asked = MagicMock()
        bot.sidebar.ask_in_chat = asked  # type: ignore[method-assign]
        await bot.on_message(message(author=DM))
        asked.assert_called_once_with(table, DM, "does fireball need line of sight")
