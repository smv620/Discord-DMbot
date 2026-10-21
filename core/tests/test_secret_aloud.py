"""The secret-said-aloud question (#1407): finding the secret names players said, reading a
campaign's secret names, the after-session step, and the two buttons. No AI anywhere."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.secret_aloud import (
    MAX_ASKED,
    SecretName,
    players_lines,
    secret_names,
    spoken,
)
from dmbot.transcript.models import Line
from dmbot.ui.secret_aloud import (
    NO_CAMPAIGN,
    NO_MEMORY,
    NOT_YOURS,
    SecretAloudButton,
    kept_text,
    more_text,
    opened_text,
    question_text,
    secret_aloud_view,
)
from tests.test_memory_store import GUILD_A, MemoryTest

PLAYER, OTHER, DM_ID, BOT = 11, 12, 7, 99
AURIL = SecretName("a" * 32, "Auril")


def line(user: int, text: str, **kw: Any) -> Line:
    return Line(1000, user, text, text, **kw)


class Finding(unittest.TestCase):
    def test_a_player_saying_a_secret_name_is_found_with_how_often(self) -> None:
        got = spoken([AURIL], ["We should ask Auril.", "Auril again, and AURIL!", "nothing"])
        self.assertEqual([(s.text, s.times) for s in got], [("Auril", 3)])

    def test_only_whole_words_count(self) -> None:
        self.assertEqual(spoken([AURIL], ["Aurilia and Laurila"]), [])
        self.assertEqual(len(spoken([AURIL], ["auril's frost", "the Auril-touched"])), 1)

    def test_case_accents_and_apostrophes_are_ignored_as_names_are_everywhere(self) -> None:
        secret = SecretName("b" * 32, "Ka'zeth")
        self.assertEqual(spoken([secret], ["Kazeth", "KA-ZETH"])[0].times, 2)
        accent = SecretName("c" * 32, "Élowen")
        self.assertEqual(len(spoken([accent], ["elowen came"])), 1)

    def test_a_name_of_several_words_must_be_said_together(self) -> None:
        secret = SecretName("d" * 32, "Mother Ilse")
        self.assertEqual(len(spoken([secret], ["I saw Mother Ilse today"])), 1)
        self.assertEqual(spoken([secret], ["Mother said Ilse is here"]), [])

    def test_a_secret_spelling_is_its_own_name(self) -> None:
        both = [AURIL, SecretName("e" * 32, "The Frostmaiden")]
        got = spoken(both, ["The Frostmaiden is here"])
        self.assertEqual([s.text for s in got], ["The Frostmaiden"])

    def test_most_said_first_and_no_more_than_five(self) -> None:
        names = [SecretName(f"{n:032x}", f"Name{chr(97 + n)}x") for n in range(8)]
        texts = [f"Name{chr(97 + n)}x " * (n + 1) for n in range(8)]
        got = spoken(names, texts)
        self.assertEqual([s.times for s in got], [8, 7, 6, 5, 4, 3, 2, 1])  # all, best first

    def test_a_name_too_short_to_search_for_is_ignored(self) -> None:
        self.assertEqual(spoken([SecretName("f" * 32, "Al")], ["Al went home"]), [])

    def test_two_entries_sharing_a_name_are_asked_once_and_both_are_kept(self) -> None:
        twins = [AURIL, SecretName("9" * 32, "auril"), SecretName("8" * 32, "Auril's")]
        (said,) = spoken(twins, ["Auril"])
        self.assertEqual(said.alias_id, AURIL.alias_id)  # the first one is what the buttons carry
        self.assertEqual(said.alias_ids, (AURIL.alias_id, "9" * 32, "8" * 32))
        self.assertEqual((said.entries, said.times), (3, 1))
        (alone,) = spoken([AURIL], ["Auril"])
        self.assertEqual((alone.entries, alone.alias_ids), (1, (AURIL.alias_id,)))

    def test_nothing_said_or_nothing_secret_finds_nothing(self) -> None:
        self.assertEqual(spoken([], ["Auril"]), [])
        self.assertEqual(spoken([AURIL], []), [])


class WhoseWords(unittest.TestCase):
    def test_only_players_recorded_now_count_and_not_the_sidebar(self) -> None:
        lines = [
            line(PLAYER, "Auril"),
            line(DM_ID, "the DM says Auril"),
            line(OTHER, "someone not recorded says Auril"),
            line(PLAYER, "DMbot says Auril", sidebar="answer"),
            line(PLAYER, "off topic Auril", topic="off_topic"),
            line(PLAYER, ""),
        ]
        self.assertEqual(players_lines(lines, players={PLAYER}), ["Auril", "off topic Auril"])


class Reading(MemoryTest):
    async def test_only_confirmed_secret_names_of_confirmed_entries_of_this_campaign(self) -> None:
        mira = await self.add("Mira", type="character", role="npc", status="confirmed")
        secret = await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.memory.add_alias(
            GUILD_A, self.c, mira, "Open Name", kind="nickname", source="dm", status="confirmed"
        )
        await self.memory.add_alias(
            GUILD_A, self.c, mira, "Guess", kind="nickname", source="dm", status="proposed",
            secret=True,
        )  # fmt: skip
        guess = await self.add("Wolf", type="character", role="npc", status="proposed")
        await self.memory.add_alias(
            GUILD_A, self.c, guess, "Wolf Secret", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        other = (await self.campaigns.create(GUILD_A, "Other", 77)).id
        theirs = await self.add("Zed", campaign=other, type="character", status="confirmed")
        await self.memory.add_alias(
            GUILD_A, other, theirs, "Zed Secret", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        mine = await secret_names(self.memory, GUILD_A, self.c)
        self.assertEqual([n.text for n in mine], ["The Hooded One"])
        self.assertEqual(mine[0].alias_id, secret.value.id)
        self.assertEqual(
            [n.text for n in await secret_names(self.memory, GUILD_A, other)], ["Zed Secret"]
        )

    async def test_not_secret_any_more_clears_the_flag_through_the_change_path(self) -> None:
        mira = await self.add("Mira", type="character", role="npc", status="confirmed")
        secret = await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.memory.update_alias(GUILD_A, self.c, secret.value.id, source=DM, secret=False)
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])


class SharedSpelling(MemoryTest):
    """Two entries that keep the same name secret: one question, one answer, one undo (#1429)."""

    async def twins(self, c: str | None = None, name: str = "Hooded One") -> tuple[str, str]:
        c = c or self.c
        mira = await self.add("Mira", campaign=c, type="character", role="npc", status="confirmed")
        wolf = await self.add("Wolf", campaign=c, type="character", role="npc", status="confirmed")
        first = await self.memory.add_alias(
            GUILD_A, c, mira, name, kind="nickname", source="dm", status="confirmed", secret=True
        )
        second = await self.memory.add_alias(
            GUILD_A, c, wolf, name, kind="nickname", source="dm", status="confirmed", secret=True
        )
        return first.value.id, second.value.id

    async def test_one_press_clears_every_entry_with_the_spelling_as_one_batch(self) -> None:
        a, b = await self.twins()
        found = await secret_names(self.memory, GUILD_A, self.c)
        (said,) = spoken(found, ["the hooded one"])
        self.assertEqual(sorted(said.alias_ids), sorted([a, b]))
        done = await self.memory.make_name_not_secret(GUILD_A, self.c, said.alias_id, source=DM)
        self.assertEqual(sorted(x.id for x in done.value), sorted([a, b]))
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])
        async with self.memory._read(GUILD_A, self.c) as scope:
            cur = await scope.conn.execute(
                "SELECT count(DISTINCT batch) AS n FROM memory_changes WHERE guild_id = %s"
                " AND campaign_id = %s AND batch = %s",
                (*scope.ids, done.batch),
            )
            row = await cur.fetchone()
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                " AND batch = %s AND table_name = 'memory_aliases'",
                (*scope.ids, done.batch),
            )
            changed = await cur.fetchone()
        assert row is not None and changed is not None
        self.assertEqual((row["n"], changed["n"]), (1, 2))  # both changes in the one batch

    async def test_one_undo_puts_both_back(self) -> None:
        a, b = await self.twins()
        done = await self.memory.make_name_not_secret(GUILD_A, self.c, a, source=DM)
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)
        back = await secret_names(self.memory, GUILD_A, self.c)
        self.assertEqual(sorted(n.alias_id for n in back), sorted([a, b]))

    async def test_pressing_from_either_entry_clears_both(self) -> None:
        a, b = await self.twins()
        await self.memory.make_name_not_secret(GUILD_A, self.c, b, source=DM)
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])
        self.assertTrue(a)

    async def test_a_different_name_and_another_campaign_are_left_alone(self) -> None:
        a, _b = await self.twins()
        mira = await self.add("Zed", type="character", role="npc", status="confirmed")
        other_name = await self.memory.add_alias(
            GUILD_A, self.c, mira, "Someone Else", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        other = (await self.campaigns.create(GUILD_A, "Other", 77)).id
        theirs = await self.twins(other)
        await self.memory.make_name_not_secret(GUILD_A, self.c, a, source=DM)
        left = {n.alias_id for n in await secret_names(self.memory, GUILD_A, self.c)}
        self.assertEqual(left, {other_name.value.id})
        kept = {n.alias_id for n in await secret_names(self.memory, GUILD_A, other)}
        self.assertEqual(kept, set(theirs))

    async def test_a_rule_that_refuses_the_second_entry_leaves_the_first_secret_too(self) -> None:
        a, b = await self.twins()
        real = self.memory._change_alias
        calls: list[str] = []

        async def refuse_the_second(w: Any, alias_id: str, *args: Any) -> Any:
            calls.append(alias_id)
            if len(calls) == 2:
                raise MemoryRuleError("No.")
            return await real(w, alias_id, *args)

        with (
            patch.object(self.memory, "_change_alias", refuse_the_second),
            self.assertRaises(MemoryRuleError),
        ):
            await self.memory.make_name_not_secret(GUILD_A, self.c, a, source=DM)
        self.assertEqual(len(calls), 2)
        left = {n.alias_id for n in await secret_names(self.memory, GUILD_A, self.c)}
        self.assertEqual(left, {a, b})  # all or nothing

    async def test_only_the_dm_may_clear_them(self) -> None:
        a, b = await self.twins()
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_name_not_secret(GUILD_A, self.c, a, source="extractor")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_name_not_secret(GUILD_A, self.c, "0" * 32, source=DM)
        left = {n.alias_id for n in await secret_names(self.memory, GUILD_A, self.c)}
        self.assertEqual(left, {a, b})


def fake_table(*, session: str | None = "s1") -> Any:
    return SimpleNamespace(
        guild_id=GUILD_A, campaign_id="c" * 32, transcript_session_id=session, screen_channel_id=55
    )


def fake_bot(
    lines: list[Line], secrets: list[SecretName], agreed: set[int], visibility: str = "private"
) -> Any:
    memory = MagicMock()
    bot = SimpleNamespace(
        memory=memory,
        transcripts=SimpleNamespace(lines=AsyncMock(return_value=lines)),
        campaigns=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    dm_user_ids=frozenset({DM_ID}),
                    dm_screen_visibility=visibility,
                    name="Frostmaiden",
                )
            )
        ),
        consent=SimpleNamespace(consenting=AsyncMock(return_value=agreed)),
        post=AsyncMock(return_value=True),
        sidebar_send_dm=AsyncMock(return_value="sent"),
    )
    return bot


class AfterTheSession(unittest.IsolatedAsyncioTestCase):
    async def run_step(self, bot: Any, secrets: list[SecretName], table: Any = None) -> None:
        from unittest import mock

        with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=secrets)):
            await DMBot.ask_about_secrets(bot, table or fake_table())

    async def test_a_player_saying_a_secret_name_asks_the_dm_on_the_dm_screen_only(self) -> None:
        bot = fake_bot([line(PLAYER, "Ask Auril")], [AURIL], {PLAYER, DM_ID})
        await self.run_step(bot, [AURIL])
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[0], 55)
        self.assertEqual(bot.post.await_args.args[1], question_text("Auril"))
        self.assertIsNotNone(bot.post.await_args.kwargs["view"])
        self.assertIsNotNone(
            bot.post.await_args.kwargs["secret_of"]
        )  # pulled back if opened (#2097)

    async def test_the_dm_alone_saying_it_asks_nothing(self) -> None:
        bot = fake_bot([line(DM_ID, "Auril is the one")], [AURIL], {PLAYER, DM_ID})
        await self.run_step(bot, [AURIL])
        bot.post.assert_not_awaited()

    async def test_dmbots_own_lines_never_count(self) -> None:
        bot = fake_bot([line(BOT, "Auril", sidebar="answer")], [AURIL], {BOT, PLAYER})
        await self.run_step(bot, [AURIL])
        bot.post.assert_not_awaited()

    async def test_someone_not_recorded_now_is_ignored(self) -> None:
        bot = fake_bot([line(OTHER, "Auril")], [AURIL], {PLAYER, DM_ID})
        await self.run_step(bot, [AURIL])
        bot.post.assert_not_awaited()

    async def test_consent_is_read_after_the_lines(self) -> None:
        order: list[str] = []

        async def read_lines(*_: Any) -> list[Line]:
            order.append("lines")
            return []

        async def read_consent(*_: Any) -> set[int]:
            order.append("consent")
            return set()

        bot = fake_bot([line(PLAYER, "Auril")], [AURIL], {PLAYER})
        bot.transcripts.lines = read_lines
        bot.consent.consenting = read_consent
        await self.run_step(bot, [AURIL])
        self.assertEqual(order, ["lines", "consent"])

    async def test_no_secret_names_means_no_reads_and_no_posts(self) -> None:
        bot = fake_bot([line(PLAYER, "Auril")], [], {PLAYER})
        await self.run_step(bot, [])
        bot.transcripts.lines.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_no_stored_session_or_no_memory_does_nothing(self) -> None:
        bot = fake_bot([], [AURIL], {PLAYER})
        await self.run_step(bot, [AURIL], fake_table(session=None))
        bot.memory = None
        await self.run_step(bot, [AURIL])
        bot.post.assert_not_awaited()

    async def test_at_most_five_are_asked(self) -> None:
        names = [SecretName(f"{n:032x}", f"Name{chr(97 + n)}x") for n in range(8)]
        lines = [line(PLAYER, " ".join(n.text for n in names))]
        bot = fake_bot(lines, names, {PLAYER})
        await self.run_step(bot, names)
        self.assertEqual(bot.post.await_count, MAX_ASKED + 1)  # five questions and a line
        self.assertEqual(bot.post.await_args.args[1], more_text(3))  # saying there are 3 more


def press(
    act: str,
    user: int,
    *,
    manager: bool = False,
    memory: Any = "default",
    visibility: str = "private",
) -> tuple[Any, Any]:
    campaign = SimpleNamespace(
        guild_id=GUILD_A,
        id="c" * 32,
        dm_user_ids=frozenset({DM_ID}),
        dm_screen_visibility=visibility,
    )
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        memory=(
            SimpleNamespace(
                make_name_not_secret=AsyncMock(return_value=SimpleNamespace(value=[object()]))
            )
            if memory == "default"
            else memory
        ),
        lookup=SimpleNamespace(drop=MagicMock()),
    )
    response = SimpleNamespace(
        is_done=lambda: False, edit_message=AsyncMock(), send_message=AsyncMock()
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD_A),
        user=SimpleNamespace(id=user, guild_permissions=SimpleNamespace(manage_guild=manager)),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return SecretAloudButton(act, "c" * 32, "a" * 32), (bot, interaction)


class WhenTheScreenIsOpen(unittest.IsolatedAsyncioTestCase):
    async def test_the_question_goes_to_the_dms_privately_when_players_may_peek(self) -> None:
        bot = fake_bot([line(PLAYER, "Ask Auril")], [AURIL], {PLAYER, DM_ID}, visibility="peek")
        from unittest import mock

        with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=[AURIL])):
            await DMBot.ask_about_secrets(bot, fake_table())
        bot.post.assert_not_awaited()  # the name is not on the screen
        bot.sidebar_send_dm.assert_awaited_once()
        self.assertEqual(bot.sidebar_send_dm.await_args.args[0], DM_ID)

    async def test_when_no_dm_can_be_reached_the_screen_says_so_without_the_name(self) -> None:
        from dmbot.bot import SECRET_QUESTION_WAITING

        bot = fake_bot([line(PLAYER, "Ask Auril")], [AURIL], {PLAYER, DM_ID}, visibility="peek")
        bot.sidebar_send_dm = AsyncMock(return_value="forbidden")
        from unittest import mock

        with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=[AURIL])):
            await DMBot.ask_about_secrets(bot, fake_table())
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[1], SECRET_QUESTION_WAITING)
        self.assertNotIn("Auril", bot.post.await_args.args[1])

    async def test_a_send_that_only_failed_says_nothing_on_the_screen(self) -> None:
        bot = fake_bot([line(PLAYER, "Ask Auril")], [AURIL], {PLAYER, DM_ID}, visibility="peek")
        bot.sidebar_send_dm = AsyncMock(return_value="failed")
        from unittest import mock

        with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=[AURIL])):
            await DMBot.ask_about_secrets(bot, fake_table())
        bot.post.assert_not_awaited()  # nothing for the DM to change; it is asked next time

    async def test_the_private_question_carries_the_server_and_the_campaigns_name(self) -> None:
        bot = fake_bot([line(PLAYER, "Ask Auril")], [AURIL], {PLAYER, DM_ID}, visibility="open")
        from unittest import mock

        with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=[AURIL])):
            await DMBot.ask_about_secrets(bot, fake_table())
        _dm, text, view = bot.sidebar_send_dm.await_args.args
        self.assertIn("(campaign Frostmaiden)", text)
        self.assertIn("Sent to you alone", text)
        self.assertTrue(all(str(GUILD_A) in c.item.custom_id for c in view.children))

    async def test_a_button_pressed_on_a_screen_that_has_opened_up_writes_no_name(self) -> None:
        from dmbot.dm_screen import secret_posts

        button, (bot, interaction) = press("keep", DM_ID, visibility="open")
        await button.callback(interaction)
        kw = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kw["content"], secret_posts.HIDDEN)
        bot.memory.make_name_not_secret.assert_not_awaited()


class InAPrivateMessage(unittest.IsolatedAsyncioTestCase):
    async def test_the_id_carries_the_server_and_the_old_id_still_parses(self) -> None:
        import re

        from dmbot.ui.secret_aloud import SecretAloudButton as Button

        new = Button("keep", "c" * 32, "a" * 32, 5).item.custom_id or ""
        self.assertEqual(new, f"dmbot:sa:k:{'c' * 32}:{'a' * 32}:5")
        longest = Button("open", "c" * 32, "a" * 32, 10**19 - 1).item.custom_id
        self.assertLessEqual(len(longest or ""), 100)  # a 19-digit server id still fits
        template = Button.__discord_ui_compiled_template__
        found = template.fullmatch(new)
        assert found is not None
        self.assertEqual((found["act2"], found["guild"]), ("k", "5"))
        old = f"dmbot:secret:keep:{'c' * 32}:{'a' * 32}"  # the id sent before the server was on it
        found = template.fullmatch(old)
        assert found is not None and found["guild"] is None and found["act"] == "keep"
        self.assertIsNone(re.fullmatch(r"x", "y"))

    async def test_a_press_in_a_private_message_finds_the_campaign_by_the_servers_id(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        button.guild_id = GUILD_A  # as parsed from the new id
        interaction.guild = None  # a private message: no server here
        await button.callback(interaction)
        bot.campaigns.get.assert_awaited_once_with(GUILD_A, "c" * 32)
        self.assertIn(
            "stays secret", interaction.response.edit_message.await_args.kwargs["content"]
        )

    async def test_an_old_button_in_a_private_message_has_no_server_and_says_so(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        interaction.guild = None
        await button.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        bot.campaigns.get.assert_not_awaited()

    async def test_a_press_in_another_server_is_not_this_campaigns(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        button.guild_id = GUILD_A + 1
        await button.callback(interaction)  # interaction.guild is GUILD_A
        bot.campaigns.get.assert_not_awaited()

    def test_the_private_question_names_the_campaign_and_says_it_was_sent_alone(self) -> None:
        text = question_text("Auril", private=True, campaign_name="Frostmaiden")
        self.assertIn("(campaign Frostmaiden)", text)
        self.assertIn("Sent to you alone", text)
        self.assertNotIn("Sent to you alone", question_text("Auril"))

    def test_an_answered_keep_message_does_not_repeat_the_name(self) -> None:
        self.assertNotIn("Auril", kept_text())


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_not_secret_any_more_clears_the_flag_as_the_dm(self) -> None:
        button, (bot, interaction) = press("open", DM_ID)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_awaited_once_with(
            GUILD_A, "c" * 32, "a" * 32, source=DM
        )
        bot.lookup.drop.assert_called_once()
        self.assertEqual(
            interaction.response.edit_message.await_args.kwargs["content"], opened_text("That name")
        )
        self.assertIsNone(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_keep_it_secret_changes_nothing_and_closes_the_question(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_not_awaited()
        self.assertEqual(
            interaction.response.edit_message.await_args.kwargs["content"], kept_text()
        )

    async def test_only_a_dm_of_the_campaign_can_press_not_a_server_manager(self) -> None:
        for act in ("keep", "open"):
            button, (bot, interaction) = press(act, PLAYER, manager=True)
            await button.callback(interaction)
            bot.memory.make_name_not_secret.assert_not_awaited()
            interaction.response.edit_message.assert_not_awaited()
            self.assertEqual(interaction.response.send_message.await_args.args[0], NOT_YOURS)

    async def test_a_rule_the_memory_refuses_is_told_and_the_question_stays(self) -> None:
        button, (bot, interaction) = press("open", DM_ID)
        bot.memory.make_name_not_secret.side_effect = MemoryRuleError("That name is gone.")
        await button.callback(interaction)
        interaction.response.edit_message.assert_not_awaited()
        self.assertEqual(interaction.response.send_message.await_args.args[0], "That name is gone.")

    async def test_without_memory_nothing_is_changed(self) -> None:
        button, (_bot, interaction) = press("open", DM_ID, memory=None)
        await button.callback(interaction)
        self.assertEqual(interaction.response.send_message.await_args.args[0], NO_MEMORY)

    async def test_the_reply_names_the_name_the_question_was_about(self) -> None:
        button, (_bot, interaction) = press("open", DM_ID)
        interaction.message = SimpleNamespace(content=question_text("Auril"))
        await button.callback(interaction)
        self.assertIn("**Auril**", interaction.response.edit_message.await_args.kwargs["content"])

    async def test_a_campaign_that_is_gone_is_said_so(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        bot.campaigns.get = AsyncMock(return_value=None)
        await button.callback(interaction)
        self.assertEqual(interaction.response.send_message.await_args.args[0], NO_CAMPAIGN)

    def test_the_question_says_what_each_button_does_and_who_can_read_the_transcript(self) -> None:
        text = question_text("Auril")
        for needed in ("anyone in this server can read", "Keep it secret", "Not secret any more"):
            self.assertIn(needed, text)

    def test_the_question_says_when_several_entries_share_the_name(self) -> None:
        self.assertNotIn("names)", question_text("Auril"))
        self.assertNotIn("covers", question_text("Auril"))
        self.assertIn("**Auril** (two names)", question_text("Auril", 2))
        self.assertIn("DMbot has it twice. Your answer covers both.", question_text("Auril", 2))
        self.assertIn("**Auril** (3 names)", question_text("Auril", 3))
        self.assertIn("DMbot has it 3 times. Your answer covers all 3.", question_text("Auril", 3))
        self.assertIn("covers both copies", opened_text("Auril", 2))
        self.assertIn("puts all 3 back", opened_text("Auril", 3))
        self.assertEqual(opened_text("Auril"), opened_text("Auril", 1))

    async def test_the_reply_says_how_many_names_were_cleared(self) -> None:
        button, (bot, interaction) = press("open", DM_ID)
        bot.memory.make_name_not_secret.return_value = SimpleNamespace(value=[1, 2])
        interaction.message = SimpleNamespace(content=question_text("Auril", 2))
        await button.callback(interaction)
        self.assertEqual(
            interaction.response.edit_message.await_args.kwargs["content"],
            opened_text("Auril", 2),
        )

    def test_the_view_has_the_two_buttons_with_ids_that_survive_a_restart(self) -> None:
        view = secret_aloud_view("c" * 32, "a" * 32)
        ids = [cast(Any, item).custom_id or "" for item in view.children]
        self.assertEqual(
            ids,
            [
                f"dmbot:secret:keep:{'c' * 32}:{'a' * 32}",
                f"dmbot:secret:open:{'c' * 32}:{'a' * 32}",
            ],
        )
        self.assertTrue(all(len(i) <= 100 for i in ids))

    def test_a_name_in_the_question_cannot_break_the_markdown(self) -> None:
        self.assertIn("\\*", question_text("*x*"))


if __name__ == "__main__":
    unittest.main()


class PossessiveAndHiddenLines(unittest.TestCase):
    def test_a_secret_with_its_own_apostrophe_s_is_found(self) -> None:
        names = [SecretName("a", "Mordenkainen's Tower"), SecretName("b", "Dragon's Rest")]
        got = spoken(names, ["We go to Mordenkainen's Tower", "Dragon's Rest is near", "no"])
        self.assertEqual({s.alias_id for s in got}, {"a", "b"})

    def test_an_off_topic_line_and_the_as_heard_words_are_read(self) -> None:
        line = Line(1, 5, "we saw auril", "we saw Oriel", topic="off_topic")
        got = players_lines([line], players={5})
        self.assertEqual(got, ["we saw Oriel", "we saw auril"])
