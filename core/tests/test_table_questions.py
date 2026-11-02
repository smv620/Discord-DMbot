"""Table questions (#1187), the parts that need no database: the answer engine's table mode, the
link allowlist and the bot's hook for messages typed in the DMbot chat."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

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
    text: str = "Hey DMbot, does fireball need line of sight?",
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
    """#3212: the table's chat is the channel the DM picked, else the voice channel's own chat."""

    def setUp(self) -> None:
        self.bot = make_bot()
        self.table = make_table()
        self.table.chat_channel_id = CHAT
        self.table.listening = True
        self.bot.tables[GUILD] = self.table
        self.asked = MagicMock()
        self.bot.sidebar.ask_in_chat = self.asked  # type: ignore[method-assign]

    def test_a_question_there_is_asked_for_its_author(self) -> None:
        self.assertTrue(self.bot._hear_table_question(message()))
        self.asked.assert_called_once_with(
            self.table, PLAYER, "Hey DMbot, does fireball need line of sight?"
        )

    def test_the_voice_channels_own_chat_counts_when_none_was_picked(self) -> None:
        self.table.chat_channel_id = None
        self.assertEqual(self.table.chat_id, self.table.voice_channel_id)
        self.assertTrue(self.bot._hear_table_question(message(channel=self.table.voice_channel_id)))
        self.asked.assert_called_once()

    def test_ordinary_chat_is_left_to_the_typed_chat_path(self) -> None:
        self.assertFalse(self.bot._hear_table_question(message("we go through the north door")))
        self.asked.assert_not_called()

    def test_a_question_mark_alone_does_not_call_dmbot(self) -> None:
        for text in ("anyone got a spare d20?", "Mira, are you casting?", "who goes first?"):
            for who in (PLAYER, DM):
                self.assertFalse(self.bot._hear_table_question(message(text, author=who)))
        self.asked.assert_not_called()  # read as ordinary typed chat, no Q line, no limit used

    def test_dmbot_at_the_start_calls_it(self) -> None:
        text = "DMbot what's the range of misty step?"
        self.assertTrue(self.bot._hear_table_question(message(text)))
        self.asked.assert_called_once_with(self.table, PLAYER, text)

    def test_bots_webhooks_and_dmbot_itself_are_never_answered(self) -> None:
        cast(Any, self.bot._connection).user = SimpleNamespace(id=999)
        for msg in (message(bot=True), message(webhook=5), message(author=999)):
            self.assertFalse(self.bot._hear_table_question(msg))
        self.asked.assert_not_called()

    def test_not_while_dmbot_is_not_listening(self) -> None:
        self.table.listening = False
        self.assertFalse(self.bot._hear_table_question(message()))
        self.asked.assert_not_called()

    def test_only_this_campaigns_chat_counts(self) -> None:
        self.assertFalse(self.bot._hear_table_question(message(channel=556)))  # another campaign's
        self.asked.assert_not_called()

    def test_the_dm_screen_and_transcript_channel_are_never_read_as_chat(self) -> None:
        self.table.chat_channel_id = self.table.screen_channel_id
        self.assertFalse(
            self.bot._hear_table_question(message(channel=self.table.screen_channel_id))
        )
        self.asked.assert_not_called()

    def test_nothing_is_read_while_the_dm_has_table_chat_off(self) -> None:
        self.table.chat_on = False
        self.assertFalse(self.bot._hear_table_question(message()))
        self.asked.assert_not_called()

    def test_without_a_session_nothing_is_read(self) -> None:
        self.bot.tables.clear()
        self.assertFalse(self.bot._hear_table_question(message()))
        self.asked.assert_not_called()


class TheDmsStatements(unittest.IsolatedAsyncioTestCase):
    """#3209 in the table's chat (#3212): a DM's statement is never a table question, so it gets no
    Q line; only a message that calls DMbot is answered."""

    def setUp(self) -> None:
        self.bot = make_bot()
        self.table = make_table()
        self.table.chat_channel_id = CHAT
        self.table.listening = True
        self.bot.tables[GUILD] = self.table
        self.asked = MagicMock()
        self.bot.sidebar.ask_in_chat = self.asked  # type: ignore[method-assign]

    async def say(self, text: str, author: int = DM) -> bool:
        return bool(self.bot._hear_table_question(message(text, author=author)))

    async def test_a_statement_gets_no_question_and_is_left_to_the_typed_chat_path(self) -> None:
        for who in (DM, PLAYER):
            self.assertFalse(
                await self.say("Mira and Tam are the children of the old smith of Redfall", who)
            )
        self.asked.assert_not_called()

    async def test_a_question_to_dmbot_or_with_a_question_mark_is_answered(self) -> None:
        for text in (
            "Hey DMbot, does fireball need line of sight?",
            "Hey DMbot, what does grappling need",
            "DMbot, how far can a rogue jump",
        ):
            self.asked.reset_mock()
            self.assertTrue(await self.say(text))
            self.asked.assert_called_once_with(self.table, DM, text)

    async def test_a_short_yes_or_correct_gets_nothing_at_all(self) -> None:
        for text in ("yes", "no", "correct", "yes please do"):
            self.assertFalse(await self.say(text))
        self.asked.assert_not_called()

    async def test_a_message_is_handled_once(self) -> None:
        typed = MagicMock()
        self.bot._hear_typed = typed  # type: ignore[method-assign]
        self.bot._chat_channel_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await self.bot.on_message(message("Hey DMbot, a question here", author=DM))
        typed.assert_not_called()  # a question is not also read as table chat
        await self.bot.on_message(message("a statement of more than four words here", author=DM))
        typed.assert_called_once()  # ordinary chat is, once


class TheChatThroughOnMessage(unittest.IsolatedAsyncioTestCase):
    async def test_a_picked_channel_that_is_another_campaigns_screen_is_never_read(self) -> None:
        bot = make_bot()
        table = make_table()
        table.chat_channel_id = CHAT
        table.listening = True
        bot.tables[GUILD] = table
        asked = MagicMock()
        bot.sidebar.ask_in_chat = asked  # type: ignore[method-assign]
        bot._chat_channel_allowed = AsyncMock(return_value=False)  # type: ignore[method-assign]
        await bot.on_message(message())
        asked.assert_not_called()

    async def test_a_question_is_not_also_read_as_table_chat(self) -> None:
        bot = make_bot()
        table = make_table()
        table.chat_channel_id = CHAT
        table.listening = True
        bot.tables[GUILD] = table
        asked, typed = MagicMock(), MagicMock()
        bot.sidebar.ask_in_chat = asked  # type: ignore[method-assign]
        bot._hear_typed = typed  # type: ignore[method-assign]
        bot._chat_channel_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await bot.on_message(message())
        asked.assert_called_once()
        typed.assert_not_called()

    async def test_the_dm_asking_there_is_a_question_like_anyone(self) -> None:
        bot = make_bot()
        table = make_table()
        table.chat_channel_id = CHAT
        table.listening = True
        bot.tables[GUILD] = table
        asked = MagicMock()
        bot.sidebar.ask_in_chat = asked  # type: ignore[method-assign]
        bot._chat_channel_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await bot.on_message(message("Hey DMbot, does fireball need line of sight?", author=DM))
        asked.assert_called_once_with(table, DM, "Hey DMbot, does fireball need line of sight?")
