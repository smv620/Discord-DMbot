"""The secret-said-aloud question (#1407): finding the secret names players said, reading a
campaign's secret names, the after-session step, and the two buttons. No AI anywhere."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    GONE,
    KEPT,
    NOT_YOURS,
    OPENED,
    SecretAloudButton,
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
        self.assertEqual(len(got), MAX_ASKED)
        self.assertEqual([s.times for s in got], [8, 7, 6, 5, 4])

    def test_a_name_too_short_to_search_for_is_ignored(self) -> None:
        self.assertEqual(spoken([SecretName("f" * 32, "Al")], ["Al went home"]), [])

    def test_two_entries_sharing_a_name_are_asked_once(self) -> None:
        twins = [AURIL, SecretName("9" * 32, "auril")]
        self.assertEqual(len(spoken(twins, ["Auril"])), 1)

    def test_nothing_said_or_nothing_secret_finds_nothing(self) -> None:
        self.assertEqual(spoken([], ["Auril"]), [])
        self.assertEqual(spoken([AURIL], []), [])


class WhoseWords(unittest.TestCase):
    def test_only_players_recorded_now_count_and_not_the_sidebar_or_off_topic(self) -> None:
        lines = [
            line(PLAYER, "Auril"),
            line(DM_ID, "the DM says Auril"),
            line(OTHER, "someone not recorded says Auril"),
            line(PLAYER, "DMbot says Auril", sidebar="answer"),
            line(PLAYER, "off topic Auril", topic="off_topic"),
            line(PLAYER, ""),
        ]
        self.assertEqual(players_lines(lines, players={PLAYER}), ["Auril"])


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


def fake_table(*, session: str | None = "s1") -> Any:
    return SimpleNamespace(
        guild_id=GUILD_A, campaign_id="c" * 32, transcript_session_id=session, screen_channel_id=55
    )


def fake_bot(lines: list[Line], secrets: list[SecretName], agreed: set[int]) -> Any:
    memory = MagicMock()
    bot = SimpleNamespace(
        memory=memory,
        transcripts=SimpleNamespace(lines=AsyncMock(return_value=lines)),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_user_ids=frozenset({DM_ID})))
        ),
        consent=SimpleNamespace(consenting=AsyncMock(return_value=agreed)),
        post=AsyncMock(return_value=True),
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
        bot = fake_bot([line(PLAYER, "Auril")], [AURIL], {PLAYER})
        bot.transcripts.lines = AsyncMock(side_effect=lambda *a: order.append("lines") or [])
        bot.consent.consenting = AsyncMock(side_effect=lambda *a: order.append("consent") or set())
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
        self.assertEqual(bot.post.await_count, MAX_ASKED)


def press(
    act: str, user: int, *, manager: bool = False, memory: Any = "default"
) -> tuple[Any, Any]:
    campaign = SimpleNamespace(guild_id=GUILD_A, id="c" * 32, dm_user_ids=frozenset({DM_ID}))
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        memory=SimpleNamespace(update_alias=AsyncMock()) if memory == "default" else memory,
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


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_not_secret_any_more_clears_the_flag_as_the_dm(self) -> None:
        button, (bot, interaction) = press("open", DM_ID)
        await button.callback(interaction)
        bot.memory.update_alias.assert_awaited_once_with(
            GUILD_A, "c" * 32, "a" * 32, source=DM, secret=False
        )
        bot.lookup.drop.assert_called_once()
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["content"], OPENED)
        self.assertIsNone(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_keep_it_secret_changes_nothing_and_closes_the_question(self) -> None:
        button, (bot, interaction) = press("keep", DM_ID)
        await button.callback(interaction)
        bot.memory.update_alias.assert_not_awaited()
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["content"], KEPT)

    async def test_only_a_dm_of_the_campaign_can_press_not_a_server_manager(self) -> None:
        for act in ("keep", "open"):
            button, (bot, interaction) = press(act, PLAYER, manager=True)
            await button.callback(interaction)
            bot.memory.update_alias.assert_not_awaited()
            interaction.response.edit_message.assert_not_awaited()
            self.assertEqual(interaction.response.send_message.await_args.args[0], NOT_YOURS)

    async def test_a_rule_the_memory_refuses_is_told_and_the_question_stays(self) -> None:
        button, (bot, interaction) = press("open", DM_ID)
        bot.memory.update_alias.side_effect = MemoryRuleError("That name is gone.")
        await button.callback(interaction)
        interaction.response.edit_message.assert_not_awaited()
        self.assertEqual(interaction.response.send_message.await_args.args[0], "That name is gone.")

    async def test_without_memory_nothing_is_changed(self) -> None:
        button, (_bot, interaction) = press("open", DM_ID, memory=None)
        await button.callback(interaction)
        self.assertEqual(interaction.response.send_message.await_args.args[0], GONE)

    def test_the_view_has_the_two_buttons_with_ids_that_survive_a_restart(self) -> None:
        view = secret_aloud_view("c" * 32, "a" * 32)
        ids = [item.custom_id for item in view.children]  # type: ignore[attr-defined]
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
