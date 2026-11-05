"""❓ Questions for you (#3435): the Questions message: its lines, its buttons (one for each kind
that waits and has a flow in Discord), and the words for the rest. Pure; no database."""

from __future__ import annotations

import unittest
from typing import Any

import discord

from dmbot.questions import board, kinds
from dmbot.questions import counts as counting
from dmbot.questions.counts import Counted

GUILD, CAMPAIGN = 111, "c" * 32


def found(**n: int) -> dict[str, Counted]:
    return {k: Counted(v) for k, v in n.items()}


def ids(view: discord.ui.View) -> list[str]:
    return [str(getattr(i, "custom_id", None) or i.item.custom_id) for i in view.children]  # type: ignore[attr-defined]


class TheText(unittest.TestCase):
    def test_a_line_for_each_kind_that_waits_in_the_agreed_words_and_order(self) -> None:
        text = board.text("Frostmaiden", found(heard=2, names=3, lookalikes=0, which=1))
        self.assertEqual(
            text.splitlines(),
            [
                "❓ **Questions for you** about **Frostmaiden**",
                "• New names to check: **3**",
                "• Did they mean…?: **2**",
                "• Which one?: **1**",
            ],
        )

    def test_nothing_waiting_says_so(self) -> None:
        self.assertEqual(
            board.text("Frostmaiden", found(names=0)).splitlines()[1], "Nothing waiting for you. 🎉"
        )
        self.assertIn("Nothing waiting", board.text("X", {}))

    def test_a_kind_without_a_button_says_where_it_is_answered(self) -> None:
        text = board.text("X", found(plot=2, recap_approve=1, rules=1, family=1, readings=4))
        self.assertIn("• Plot check: **2** (answer on the website)", text)
        self.assertIn("• Recaps to approve: **1** (answer on the DM screen)", text)
        self.assertIn("• House rule updates: **1** (answer on the DM screen)", text)
        self.assertIn("• Family ties: **1**", text)
        self.assertNotIn("• Family ties: **1** (", text)  # it has its own button now (#3463)
        self.assertIn("• What people believe: **4** (answer on the website)", text)

    def test_a_campaign_name_cannot_change_the_message(self) -> None:
        text = board.text("**@everyone** " + "x" * 200, found(names=1))
        self.assertNotIn("@everyone", text.replace("@​everyone", ""))
        self.assertLessEqual(len(text), 400)


class TheButtons(unittest.TestCase):
    def test_one_button_for_each_waiting_kind_that_has_a_flow(self) -> None:
        view = board.view(GUILD, CAMPAIGN, found(names=1, heard=1, lookalikes=2, claims=3))
        self.assertEqual(
            ids(view),
            [
                f"dmbot:names:recap:{GUILD}:{CAMPAIGN}",
                f"dmbot:names:heard:{CAMPAIGN}",
                f"dmbot:questions:look:{CAMPAIGN}",
                f"dmbot:claims:review:{CAMPAIGN}",
            ],
        )

    def test_family_ties_get_their_own_button(self) -> None:
        view = board.view(GUILD, CAMPAIGN, found(family=2, claims=1))
        self.assertEqual(
            ids(view), [f"dmbot:family:ties:{CAMPAIGN}", f"dmbot:claims:review:{CAMPAIGN}"]
        )

    def test_connections_and_which_one_share_the_one_connections_button(self) -> None:
        for waiting in (found(connections=2), found(which=1), found(connections=1, which=1)):
            self.assertEqual(
                ids(board.view(GUILD, CAMPAIGN, waiting)), [f"dmbot:linksready:{CAMPAIGN}"]
            )

    def test_nothing_waiting_or_only_website_kinds_means_no_buttons(self) -> None:
        self.assertEqual(ids(board.view(GUILD, CAMPAIGN, found(names=0))), [])
        self.assertEqual(ids(board.view(GUILD, CAMPAIGN, found(plot=3, rules=1))), [])

    def test_the_most_buttons_fit_two_rows(self) -> None:
        everything = found(**{k: 1 for k in kinds.ORDER})
        self.assertLessEqual(len(board.view(GUILD, CAMPAIGN, everything).children), 10)

    def test_the_look_alikes_button_carries_the_campaign_for_a_restart(self) -> None:
        item = board.LookAlikesButton(CAMPAIGN)
        self.assertEqual(item.item.custom_id, f"dmbot:questions:look:{CAMPAIGN}")
        self.assertLessEqual(len(item.item.custom_id or ""), 100)


class TheCommand(unittest.IsolatedAsyncioTestCase):
    """/dmbot questions: the list is made in the DM's private chat; a player is told it is not
    theirs."""

    def interaction(self, user: int, round_: object) -> object:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        campaign = SimpleNamespace(
            guild_id=GUILD, id=CAMPAIGN, name="Frostmaiden", dm_user_ids=frozenset({7})
        )
        bot = MagicMock()
        bot.question_round = round_
        bot.campaigns.list_campaigns = AsyncMock(return_value=[campaign])
        it = MagicMock(client=bot, guild=MagicMock(id=GUILD), user=MagicMock(id=user))
        it.response.defer = AsyncMock()
        it.followup.send = AsyncMock()
        return it

    async def test_a_player_is_told_it_is_not_theirs_and_nothing_is_made(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot.questions import discord as qd

        tell = AsyncMock()
        with patch.object(qd, "_tell", tell):
            await qd.dmbot_questions.callback(self.interaction(8, object()))  # type: ignore[arg-type,call-arg]
        assert tell.await_args is not None
        self.assertEqual(tell.await_args.args[1], qd.NOT_A_DM)

    async def test_a_dm_gets_the_list_and_a_link_to_it(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.questions import discord as qd
        from dmbot.questions.notices import Stored
        from dmbot.questions.timer import Notice

        made: list[object] = []

        async def show(user, campaign, found, message_id, *, create):  # type: ignore[no-untyped-def]
            made.append((user, campaign.id, message_id, create))
            return 55, "https://discord.test/55"

        round_ = SimpleNamespace(
            sources=SimpleNamespace(),
            counts=SimpleNamespace(write=AsyncMock()),
            notices=SimpleNamespace(
                get=AsyncMock(return_value=Stored(Notice())), set_message=AsyncMock()
            ),
            sender=SimpleNamespace(board=show),
            latest={},
        )
        it = self.interaction(7, round_)
        from unittest.mock import patch

        with patch.object(counting, "count_kinds", AsyncMock(return_value=found(names=2))):
            await qd.dmbot_questions.callback(it)  # type: ignore[arg-type,call-arg]
        self.assertEqual(made, [(7, CAMPAIGN, None, True)])
        text = it.followup.send.await_args.args[0]  # type: ignore[attr-defined]
        self.assertIn("[Open the list](https://discord.test/55)", text)
        round_.notices.set_message.assert_awaited_once_with(GUILD, CAMPAIGN, 7, 55)


class TheLookAlikesButton(unittest.IsolatedAsyncioTestCase):
    async def test_it_answers_privately_first_so_the_questions_message_is_never_replaced(
        self,
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        order: list[str] = []
        interaction = MagicMock()

        async def defer(**kw: object) -> None:
            order.append(f"defer ephemeral={kw.get('ephemeral')}")

        async def show(_it: object, _campaign: str) -> None:
            order.append("show")

        interaction.response.defer = defer
        with patch("dmbot.ui.look_alikes.show_look_alikes", AsyncMock(side_effect=show)):
            await board.LookAlikesButton(CAMPAIGN).callback(interaction)
        self.assertEqual(order, ["defer ephemeral=True", "show"])
        interaction.response.edit_message.assert_not_called()


class TheSender(unittest.IsolatedAsyncioTestCase):
    """DiscordSender.board: edit in place, make one when there is none or it was deleted."""

    def sender(self, partial: object):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from dmbot.questions.discord import DiscordSender

        channel = MagicMock()
        channel.get_partial_message = MagicMock(return_value=partial)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=99, jump_url="https://j/99"))
        user = MagicMock(dm_channel=channel)
        bot = MagicMock()
        bot.get_user = MagicMock(return_value=user)
        return DiscordSender(bot), channel

    def campaign(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(guild_id=GUILD, id=CAMPAIGN, name="Frostmaiden")

    async def test_an_existing_message_is_edited_not_remade(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        partial = SimpleNamespace(edit=AsyncMock(), jump_url="https://j/5")
        sender, channel = self.sender(partial)
        got = await sender.board(7, self.campaign(), found(names=1), 5, create=True)
        self.assertEqual(got, (5, "https://j/5"))
        partial.edit.assert_awaited_once()
        channel.send.assert_not_called()

    async def test_a_deleted_message_is_made_again_only_when_asked(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        gone = discord.NotFound(MagicMock(status=404), "gone")
        partial = SimpleNamespace(edit=AsyncMock(side_effect=gone), jump_url="x")
        sender, channel = self.sender(partial)
        self.assertEqual(
            await sender.board(7, self.campaign(), found(names=1), 5, create=False), (None, "")
        )
        channel.send.assert_not_called()
        self.assertEqual(
            await sender.board(7, self.campaign(), found(names=1), 5, create=True),
            (99, "https://j/99"),
        )

    async def test_a_closed_private_chat_loses_nothing_and_raises_nothing(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        sender, channel = self.sender(SimpleNamespace())
        channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
        got = await sender.board(7, self.campaign(), found(names=1), None, create=True)
        self.assertEqual(got, (None, ""))


class TheCardButtonsPress(unittest.IsolatedAsyncioTestCase):
    """📬 Questions for you on the settings card: a DM makes the list; anyone else is told no."""

    def interaction(self, user: int, round_: object):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        campaign = SimpleNamespace(
            guild_id=GUILD, id=CAMPAIGN, name="Frostmaiden", dm_user_ids=frozenset({7})
        )
        bot = MagicMock()
        bot.question_round = round_
        bot.campaigns.get = AsyncMock(return_value=campaign)
        it = MagicMock(client=bot, guild=MagicMock(id=GUILD), user=MagicMock(id=user))
        it.response.defer = AsyncMock()
        return it

    async def test_the_button_answers_discord_before_it_reads_the_campaign(self) -> None:
        from unittest.mock import AsyncMock, patch

        it = self.interaction(7, object())
        order: list[str] = []
        it.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))
        real = it.client.campaigns.get

        async def read(*args: object) -> object:
            order.append("read")
            return await real(*args)

        it.client.campaigns.get = read
        with patch("dmbot.questions.discord.send_lists", AsyncMock()):
            await board.QuestionsButton(CAMPAIGN).callback(it)
        self.assertEqual(order[:2], ["defer", "read"])

    async def test_a_player_is_told_it_is_not_theirs(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot.questions.discord import NOT_A_DM

        tell = AsyncMock()
        with patch("dmbot.ui.dmbot_commands._tell", tell):
            await board.QuestionsButton(CAMPAIGN).callback(self.interaction(8, object()))
        assert tell.await_args is not None
        self.assertEqual(tell.await_args.args[1], NOT_A_DM)

    async def test_a_dm_gets_the_list_made(self) -> None:
        from unittest.mock import AsyncMock, patch

        sent = AsyncMock()
        round_ = object()
        with patch("dmbot.questions.discord.send_lists", sent):
            await board.QuestionsButton(CAMPAIGN).callback(self.interaction(7, round_))
        assert sent.await_args is not None
        self.assertEqual(sent.await_args.args[1], round_)
        self.assertEqual([c.id for c in sent.await_args.args[2]], [CAMPAIGN])

    async def test_the_label_total_comes_from_the_stored_counts(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        counts = SimpleNamespace(read=AsyncMock(return_value=found(names=2, heard=3)))
        it = self.interaction(7, SimpleNamespace(counts=counts))
        self.assertEqual(await board.total_for(it, CAMPAIGN), 5)
        broken = SimpleNamespace(counts=SimpleNamespace(read=AsyncMock(side_effect=RuntimeError)))
        self.assertIsNone(await board.total_for(self.interaction(7, broken), CAMPAIGN))
        self.assertIsNone(await board.total_for(self.interaction(7, None), CAMPAIGN))

    async def test_family_ties_are_in_the_total_now_that_a_screen_answers_them(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        counts = SimpleNamespace(read=AsyncMock(return_value=found(names=2, family=3)))
        it = self.interaction(7, SimpleNamespace(counts=counts))
        self.assertEqual(await board.total_for(it, CAMPAIGN), 5)
