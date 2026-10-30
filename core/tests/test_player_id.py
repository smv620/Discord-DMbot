"""Set a character's player by typing their Discord ID, confirmed by name (#3004): the one lookup
(a made-up ID, a bot, an unknown ID, a failed lookup, someone not in the server), and the flow that
links only after "✅ That's them", stores the ID and never the name. Invented names and IDs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.ui import player_id as pid
from tests.test_memory_names import GUILD, NamesTest

ID = 123456789012345678


def user(name: str = "SomeUser", *, bot: bool = False, global_name: str | None = None) -> Any:
    return SimpleNamespace(id=ID, name=name, global_name=global_name, bot=bot)


def client(found: Any = None, *, member: Any = "in", error: Exception | None = None) -> Any:
    """A Discord client whose user lookup answers `found`, or raises `error`. `member`: "in" (the
    server has them), "out" (Discord says there is no such member) or "down" (the lookup fails)."""
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=object() if member == "in" else None)

    async def fetch_member(_: int) -> Any:
        if member == "out":
            raise discord.NotFound(MagicMock(status=404), "Unknown Member")
        raise discord.HTTPException(MagicMock(status=500), "down")

    guild.fetch_member = fetch_member
    c = MagicMock()
    c.get_guild = MagicMock(return_value=guild)
    c.fetch_user = AsyncMock(return_value=found, side_effect=error)
    return c


def not_found() -> discord.NotFound:
    return discord.NotFound(MagicMock(status=404), "Unknown User")


class Lookups(NamesTest):
    async def test_a_typed_id_or_a_pasted_mention_is_an_id_and_nothing_else_is(self) -> None:
        self.assertEqual(pid.parse_id(f" {ID} "), ID)
        self.assertEqual(pid.parse_id(f"<@{ID}>"), ID)
        self.assertEqual(pid.parse_id(f"<@!{ID}>"), ID)
        for bad in ("", "Mia", "12345", f"{ID}x", "-1", "9" * 30, f"{ID} {ID}"):
            self.assertIsNone(pid.parse_id(bad), bad)

    async def test_a_known_person_is_found_with_the_name_discord_shows(self) -> None:
        got = await pid.look_up(client(user("some_user", global_name="SomeUser")), GUILD, str(ID))
        self.assertEqual(
            (got.kind, got.user_id, got.name, got.in_server), (pid.FOUND, ID, "SomeUser", True)
        )
        plain = await pid.look_up(client(user("some_user")), GUILD, str(ID))
        self.assertEqual(plain.name, "some_user")  # the username when there is no display name

    async def test_someone_not_in_the_server_is_still_found_and_says_so(self) -> None:
        got = await pid.look_up(client(user(), member="out"), GUILD, str(ID))
        self.assertEqual((got.kind, got.in_server), (pid.FOUND, False))
        self.assertIn("isn't in this server yet. Link them anyway?", pid.confirm_text(got))
        self.assertIn("It does not record anyone.", pid.confirm_text(got))

    async def test_an_unsure_membership_never_claims_they_are_away(self) -> None:
        got = await pid.look_up(client(user(), member="down"), GUILD, str(ID))
        self.assertTrue(got.in_server)

    async def test_a_bot_an_unknown_id_a_failed_lookup_and_not_an_id_each_link_nothing(
        self,
    ) -> None:
        self.assertEqual((await pid.look_up(client(user(bot=True)), GUILD, str(ID))).kind, pid.BOT)
        self.assertEqual(
            (await pid.look_up(client(error=not_found()), GUILD, str(ID))).kind, pid.UNKNOWN
        )
        down = discord.HTTPException(MagicMock(status=500), "down")
        self.assertEqual((await pid.look_up(client(error=down), GUILD, str(ID))).kind, pid.FAILED)
        c = client(user())
        self.assertEqual((await pid.look_up(c, GUILD, "Mia")).kind, pid.NOT_AN_ID)
        c.fetch_user.assert_not_awaited()  # not an ID: Discord is not even asked


class Flow(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.linked = AsyncMock()
        self.allowed = AsyncMock(return_value=True)

    def use(self, discord_client: Any) -> None:
        """Let the test bot answer Discord's user lookup as `discord_client` does."""
        self.bot.fetch_user = discord_client.fetch_user  # type: ignore[method-assign]
        self.bot.get_guild = discord_client.get_guild  # type: ignore[method-assign]

    async def submit(self, discord_client: Any, text: str) -> Any:
        it = self.it()
        self.use(discord_client)
        it.response.defer = AsyncMock()
        it.edit_original_response = AsyncMock()
        it.followup.send = AsyncMock()
        form = pid.PlayerIdForm(GUILD, self.allowed, self.linked)
        form.box = SimpleNamespace(value=text)  # type: ignore[assignment]
        await form.on_submit(it)
        return it

    async def suggest(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name=name, source="scan",
            description="Heard 3 times",
        )  # fmt: skip
        return str(written.value.id)

    async def review(self) -> tuple[Any, Any]:
        from dmbot.ui import names as ui

        it = self.it()
        await ui.start_review(it, self.campaign.id)
        return it.response.sent[0][1]["view"], it

    def said(self, it: Any) -> str:
        """The refusal sent privately, or the confirmation that replaced the step in place."""
        if it.followup.send.await_args is not None:
            return str(it.followup.send.await_args.args[0])
        if it.response.sent:  # (the stand-in has not "answered" for a deferred update)
            return str(it.response.sent[-1][0])
        return str(it.edit_original_response.await_args.kwargs["content"])

    def shown_view(self, it: Any) -> Any:
        return it.edit_original_response.await_args.kwargs["view"]

    async def test_nothing_links_before_the_confirmation(self) -> None:
        it = await self.submit(client(user("SomeUser")), str(ID))
        self.assertIn("Please confirm: SomeUser", self.said(it))
        self.linked.assert_not_awaited()
        view = self.shown_view(it)
        press = self.it()
        await view._yes(press)
        self.linked.assert_awaited_once_with(press, ID, "SomeUser")

    async def test_cancel_links_nobody(self) -> None:
        it = await self.submit(client(user()), str(ID))
        view = self.shown_view(it)
        press = self.it()
        await view._cancel(press)
        self.linked.assert_not_awaited()
        self.assertIn("Nobody was linked", press.response.edited[0][0])

    async def test_cancel_goes_back_to_the_step_it_came_from(self) -> None:
        back = AsyncMock()
        it = self.it()
        it.response.defer = AsyncMock()
        it.edit_original_response = AsyncMock()
        it.followup.send = AsyncMock()
        self.use(client(user()))
        form = pid.PlayerIdForm(GUILD, self.allowed, self.linked, back)
        form.box = SimpleNamespace(value=str(ID))  # type: ignore[assignment]
        await form.on_submit(it)
        press = self.it()
        await self.shown_view(it)._cancel(press)
        back.assert_awaited_once_with(press)
        self.linked.assert_not_awaited()

    async def test_the_confirmation_replaces_the_step_it_does_not_sit_beside_it(self) -> None:
        it = await self.submit(client(user()), str(ID))
        it.response.defer.assert_awaited_once_with()  # an update of the step's own message
        it.followup.send.assert_not_awaited()

    async def test_a_refused_id_gives_its_words_and_no_confirmation(self) -> None:
        for found, text, words in (
            (client(user(bot=True)), str(ID), pid.BOT_TEXT),
            (client(error=not_found()), str(ID), pid.UNKNOWN_TEXT),
            (
                client(error=discord.HTTPException(MagicMock(status=500), "x")),
                str(ID),
                pid.FAILED_TEXT,
            ),
            (client(user()), "hello", pid.NOT_AN_ID_TEXT),
        ):
            it = await self.submit(found, text)
            self.assertEqual(self.said(it), words)
            it.edit_original_response.assert_not_awaited()  # the step stays as it was
        self.linked.assert_not_awaited()

    async def test_only_the_campaigns_dm_gets_a_lookup(self) -> None:
        self.allowed.return_value = False
        c = client(user())
        await self.submit(c, str(ID))
        c.fetch_user.assert_not_awaited()

    async def test_no_id_and_no_name_reach_the_log(self) -> None:
        with self.assertLogs("dmbot.ui.player_id", level=logging.INFO) as logs:
            await self.submit(client(user("SomeUser")), str(ID))
        text = "\n".join(logs.output)
        self.assertNotIn(str(ID), text)
        self.assertNotIn("SomeUser", text)

    async def asking(self, name: str, *later: str) -> tuple[Any, Any]:
        """The review at "Who plays <name>?" (with `later` suggestions waiting behind it): its
        view and the ⌨️ button."""
        from dmbot.ui import names as ui

        for waiting in (name, *later):
            await self.suggest(waiting)
        view, _ = await self.review()
        await view._yes(self.it())
        view.kind = SimpleNamespace(values=[ui.PC])
        await view._kind_picked(self.it())
        labels = [getattr(c, "label", "") for c in view.children]
        self.assertIn(pid.TYPE_ID_LABEL, labels)  # beside the people menu
        button = next(c for c in view.children if getattr(c, "label", "") == pid.TYPE_ID_LABEL)
        return view, button

    async def test_a_second_confirmation_after_the_queue_moved_on_links_nothing(self) -> None:
        _view, button = await self.asking("Cerric", "Zed")
        form_press = self.it()
        form_press.response.send_modal = AsyncMock()
        await button.callback(form_press)  # opens the form (its callbacks capture "Cerric")
        form = form_press.response.send_modal.await_args.args[0]
        confirmations = []
        for _ in range(2):  # two open confirmations for the same suggestion
            it = self.it()
            it.response.defer = AsyncMock()
            it.edit_original_response = AsyncMock()
            it.followup.send = AsyncMock()
            self.use(client(user("SomeUser")))
            form.box = SimpleNamespace(value=str(ID))
            await form.on_submit(it)
            confirmations.append(self.shown_view(it))
        await confirmations[0]._yes(self.it())  # the first links Cerric and moves on to Zed
        second = self.it()
        await confirmations[1]._yes(second)  # the second must not link Zed
        names = await self.names()
        self.assertEqual(names["Cerric"].played_by, ID)
        waiting = await self.names(("proposed",))
        self.assertIn("Zed", waiting)  # still waiting: nobody was linked to it
        self.assertIsNone(waiting["Zed"].played_by)
        sent = [str(c.args[0]) for c in second.followup.send.await_args_list if c.args]
        self.assertIn("That one's already done.", sent)

    async def test_a_suggestion_is_linked_to_a_typed_id_and_only_the_id_is_stored(self) -> None:
        from dmbot.ui import names as ui

        _view, button = await self.asking("Cerric")
        form_press = self.it()
        form_press.response.send_modal = AsyncMock()
        await button.callback(form_press)
        form = form_press.response.send_modal.await_args.args[0]
        it = self.it()
        it.response.defer = AsyncMock()
        it.edit_original_response = AsyncMock()
        it.followup.send = AsyncMock()
        self.use(client(user("SomeUser"), member="out"))  # not in the server yet
        form.box = SimpleNamespace(value=str(ID))
        await form.on_submit(it)
        self.assertIn("isn't in this server yet", self.said(it))
        press = self.it()
        await self.shown_view(it)._yes(press)
        cerric = (await self.names())["Cerric"]
        self.assertEqual((cerric.kind_key, cerric.played_by), (ui.PC, ID))
        self.assertIn("played by **SomeUser**", press.response.edited[0][0])
        self.assertNotIn("SomeUser", repr(cerric))  # the name is shown, never kept

    async def test_the_add_a_players_character_step_opens_the_character_form_after_the_check(
        self,
    ) -> None:
        from dmbot.ui.names import CharacterForm, PlayerPicker

        picker = PlayerPicker(self.campaign.id, GUILD)
        button = next(c for c in picker.children if getattr(c, "label", "") == pid.TYPE_ID_LABEL)
        press = self.it()
        press.response.send_modal = AsyncMock()
        await button.callback(press)
        form = press.response.send_modal.await_args.args[0]
        it = self.it()
        it.response.defer = AsyncMock()
        it.edit_original_response = AsyncMock()
        self.use(client(user("SomeUser")))
        form.box = SimpleNamespace(value=str(ID))
        await form.on_submit(it)
        yes = self.it()
        yes.response.send_modal = AsyncMock()
        await self.shown_view(it)._yes(yes)
        (character_form,) = yes.response.send_modal.await_args.args
        self.assertIsInstance(character_form, CharacterForm)
        self.assertEqual(character_form.player_id, ID)  # the ID, not a name
        self.assertNotIn(
            pid.TYPE_ID_LABEL,
            [getattr(c, "label", "") for c in PlayerPicker(self.campaign.id).children],
        )

    async def test_a_name_cards_kind_change_has_the_button_and_links_the_id(self) -> None:
        from dmbot.ui import name_card

        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="npc", name="Cerric", source="dm", status="confirmed"
        )
        change = name_card.KindChange(self.campaign.id, made.value.id, "Cerric")
        change.pick = SimpleNamespace(values=["player_character"])  # type: ignore[assignment]
        await change._picked(self.it())
        button = next(c for c in change.children if getattr(c, "label", "") == pid.TYPE_ID_LABEL)
        press = self.it()
        press.response.send_modal = AsyncMock()
        await button.callback(press)
        form = press.response.send_modal.await_args.args[0]
        it = self.it()
        it.response.defer = AsyncMock()
        it.edit_original_response = AsyncMock()
        self.use(client(user("SomeUser")))
        form.box = SimpleNamespace(value=str(ID))
        await form.on_submit(it)
        self.bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
        await self.shown_view(it)._yes(self.it())
        cerric = (await self.names())["Cerric"]
        self.assertEqual(cerric.played_by, ID)
