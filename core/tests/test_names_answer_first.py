"""Names menus answer Discord before loading names (#537), with a fake clock: Discord
gives up 3 seconds after a press, and a big campaign's names can take longer to load."""

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBotTree
from dmbot.campaigns.models import Campaign
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, Alias, Entity, MemoryRuleError, name_key
from dmbot.memory.sounds import sound_codes
from dmbot.ui import dmbot_commands, logic, name_card, name_lists, names

GUILD, DM = 1, 7
DISCORD_WAITS_S = 3
SLOW_S = 4  # longer than Discord waits
BELL, ULF = "a" * 32, "b" * 32
NEW = {"ephemeral": True, "thinking": True}  # how to answer first: a new private reply
IN_PLACE: dict[str, Any] = {}  # or the message pressed, edited
CAMPAIGN = Campaign(
    "c" * 32, GUILD, "Rime", 0, None, "2024", "2014", True, frozenset({DM}), None, None, "peek"
)
ENTITIES = {
    eid: Entity(eid, "npc", name, "", CONFIRMED, None, "dm", 0)
    for eid, name in ((BELL, "Belleros"), (ULF, "Ulfgar"))
}


def _names() -> CampaignLookup:
    aliases = tuple(
        Alias(e.id, e.id, e.name, name_key(e.name), "full", None, False, CONFIRMED,
              sound_codes(e.name), "dm", 0)
        for e in ENTITIES.values()
    )  # fmt: skip
    return CampaignLookup.build(LookupData(1, tuple(ENTITIES.values()), aliases, (), ()))


class Clock:
    """Fake time, and when Discord first heard back. Like discord.py, an interaction
    can be answered once only."""

    def __init__(self) -> None:
        self.now = 0.0
        self.answered_at: float | None = None
        self.calls: list[str] = []

    def answer(self, how: str) -> None:
        if self.answered_at is None:
            self.answered_at = self.now
        self.calls.append(how)


class Response:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.done = False
        self.defer_kw: dict[str, Any] | None = None
        self.sent_kw: dict[str, Any] | None = None
        self.content: str | None = None

    def is_done(self) -> bool:
        return self.done

    def _once(self, how: str) -> None:
        assert not self.done, "answered twice (discord.InteractionResponded)"
        self.done = True
        self.clock.answer(how)

    async def defer(self, **kw: Any) -> None:
        self._once("defer")
        self.defer_kw = kw

    async def send_message(self, *args: Any, **kw: Any) -> None:
        self._once("send_message")
        self.sent_kw = {"text": args[0] if args else None, **kw}

    async def edit_message(self, *, content: str = "", **_: Any) -> None:
        self._once("edit_message")
        self.content = content


class SlowLookup:
    """The campaign's names, loaded cold: takes SLOW_S on the fake clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.fail = False

    async def get(self, guild_id: int, campaign_id: str) -> CampaignLookup:
        self.clock.now += SLOW_S
        if self.fail:
            raise ConnectionError("database away")
        return _names()

    def mark_stale(self, guild_id: int, campaign_id: str) -> None:
        pass


class AnswerFirst(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.gate: asyncio.Event | None = None

        async def slow_kinds(*_: Any, **__: Any) -> Any:
            if self.gate is not None:
                await self.gate.wait()
            self.clock.now += SLOW_S
            return SimpleNamespace(value=2)

        memory = MagicMock()
        memory.relations = AsyncMock(return_value=[])
        memory.entities = AsyncMock(return_value=[])
        memory.entity = AsyncMock(side_effect=lambda g, c, eid: ENTITIES.get(eid))
        memory.confirm_kinds = AsyncMock(side_effect=slow_kinds)
        self.lookup = SlowLookup(self.clock)
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=CAMPAIGN)),
            lookup=self.lookup,
            memory=memory,
            name_of=lambda *_: "Ann",
        )

    def it(self, kind: discord.InteractionType = discord.InteractionType.component) -> Any:
        """A new press (or a slash command), on a fresh clock."""
        self.clock.now, self.clock.answered_at, self.clock.calls = 0.0, None, []
        user = MagicMock(spec=discord.Member)
        user.id = DM
        user.guild_permissions = discord.Permissions.none()
        return SimpleNamespace(
            client=self.bot,
            type=kind,
            command=None,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=Response(self.clock),
            followup=SimpleNamespace(
                send=AsyncMock(side_effect=lambda *_, **__: self.clock.answer("followup"))
            ),
            edit_original_response=AsyncMock(
                side_effect=lambda **_: self.clock.answer("edit_original")
            ),
            message=SimpleNamespace(content="📥 Added 2 names."),
        )

    def assert_in_time(self, it: Any, *calls: str, defer: dict[str, Any] | None = None) -> None:
        self.assertIsNotNone(self.clock.answered_at)
        assert self.clock.answered_at is not None
        self.assertLess(self.clock.answered_at, DISCORD_WAITS_S)
        self.assertEqual(self.clock.calls, list(calls))
        if defer is not None:
            self.assertEqual(it.response.defer_kw, defer)

    async def test_browse_by_kind_answers_before_the_names_load(self) -> None:
        it = self.it()
        await name_lists.show_browse(it, CAMPAIGN.id)
        self.assert_in_time(it, "defer", "followup", defer=NEW)
        self.assertIsNotNone(it.followup.send.await_args.kwargs["view"])  # the list arrives

    async def test_browse_pages_answer_before_the_names_load(self) -> None:
        view = name_lists.Browse(CAMPAIGN.id, _names(), "npc")
        it = self.it()
        await view._sort(it)
        self.assert_in_time(it, "defer", "edit_original", defer=IN_PLACE)

    async def test_names_panel_answers_before_the_names_load(self) -> None:
        it = self.it()
        await names.show_home(it, CAMPAIGN.id)
        self.assert_in_time(it, "defer", "followup", defer=NEW)

    async def test_a_card_answers_before_the_names_load(self) -> None:
        it = self.it()
        await name_card.show_card(it, CAMPAIGN.id, BELL)
        self.assert_in_time(it, "defer", "followup", defer=NEW)
        it = self.it()
        await name_card.show_card(it, CAMPAIGN.id, BELL, replace=True)
        self.assert_in_time(it, "defer", "edit_original", defer=IN_PLACE)

    async def test_find_answers_once_before_the_names_load(self) -> None:
        it = self.it()  # one match opens its card: still answered once
        await name_card.show_matches(it, CAMPAIGN.id, "Belleros")
        self.assert_in_time(it, "defer", "followup", defer=NEW)

    async def test_card_steps_answer_before_the_names_load(self) -> None:
        card = name_card.NameCard(CAMPAIGN.id, BELL, others=False, longer=False)
        it = self.it()
        await card._connect(it)
        self.assert_in_time(it, "defer", "edit_original", defer=IN_PLACE)
        it = self.it()  # the card's search form, then the picked name
        await name_card.show_picks(it, CAMPAIGN.id, BELL, name_card.SAME, "Ulfgar")
        self.assert_in_time(it, "defer", "edit_original", defer=IN_PLACE)
        pick = name_card.PickOther(
            CAMPAIGN.id,
            BELL,
            name_card.SAME,
            "Belleros",
            [discord.SelectOption(label="U", value=ULF)],
        )
        pick.pick._values = [ULF]
        it = self.it()
        await pick._picked(it)
        self.assert_in_time(it, "defer", "edit_original", defer=IN_PLACE)

    def slow(self, value: Any = None) -> AsyncMock:
        """A database write or read that takes SLOW_S on the fake clock."""

        async def run(*_: Any, **__: Any) -> Any:
            self.clock.now += SLOW_S
            return value

        return AsyncMock(side_effect=run)

    async def test_every_saving_step_answers_before_the_slow_part(self) -> None:
        # #698 (from the #637 review): the steps that gained _answer_first in #537 with
        # no clock test. Each answers in place, then saves and reloads the names.
        bell = Alias("d" * 32, BELL, "Bell", "bell", "nickname", None, False, CONFIRMED,
                     sound_codes("Bell"), "dm", 0)  # fmt: skip
        memory = self.bot.memory
        memory.aliases = self.slow([bell])
        for write in ("set_entity_type", "confirm_entity", "set_main_name", "update_alias",
                      "add_alias", "update_relation"):  # fmt: skip
            setattr(memory, write, self.slow())
        memory.add_entity = self.slow(SimpleNamespace(value=ENTITIES[BELL]))
        memory.rename_entity = self.slow(SimpleNamespace(value=ENTITIES[BELL]))
        memory.add_relation = self.slow(SimpleNamespace(value=(None, [])))
        player = MagicMock(spec=discord.Member, bot=False, id=5)

        def picked(view: Any, select: Any, value: str) -> Any:
            select._values = [value]
            return view

        fix = name_card.FixSpellingForm(CAMPAIGN.id, BELL, "Belleros")
        fix.name._value = "Bellerose"
        more = name_card.AnotherNameForm(CAMPAIGN.id, BELL, "Belleros", secrets=True)
        more.other._value, more.secret._value = "Bel", ""
        kind = names.KindPicker(CAMPAIGN.id, "Kesh", [], [])
        change = name_card.KindChange(CAMPAIGN.id, BELL, "Belleros")
        one = name_card.OneName(CAMPAIGN.id, BELL, bell, secrets=True)
        links = name_card.Connect(CAMPAIGN.id, BELL, "Belleros", [("r" * 32, "knows Ulfgar")])
        steps: dict[str, Any] = {
            "KindPicker": lambda it: picked(kind, kind.pick, "npc")._picked(it),
            "NameCard._edit_others": lambda it: name_card.NameCard(
                CAMPAIGN.id, BELL, others=True, longer=False
            )._edit_others(it),
            "FixSpellingForm": fix.on_submit,
            "AnotherNameForm": more.on_submit,
            "KindChange._picked": lambda it: picked(change, change.pick, "npc")._picked(it),
            "KindChange._player_picked": lambda it: change._player_picked(it, player),
            "OneName._main": one._main,
            "OneName._secret": one._secret,
            "OneName._not_this": one._not_this,
            "Connect._remove_picked": lambda it: picked(
                links, links.remove, "r" * 32
            )._remove_picked(it),
            "connect": lambda it: name_card.connect(it, CAMPAIGN.id, BELL, "knows", ULF),
        }
        for name, step in steps.items():
            with self.subTest(name):
                it = self.it()
                with self.assertNoLogs("dmbot", "ERROR"):
                    await step(it)
                self.assertEqual(self.clock.calls[0], "defer")
                self.assertEqual(it.response.defer_kw, IN_PLACE)
                assert self.clock.answered_at is not None
                self.assertLess(self.clock.answered_at, DISCORD_WAITS_S)
                self.assertGreaterEqual(self.clock.now, SLOW_S)  # the slow part came after

    async def test_a_failed_load_after_answering_says_so(self) -> None:
        self.lookup.fail = True
        it = self.it()
        with self.assertLogs("dmbot.ui.name_lists", "ERROR"):
            await name_lists.show_browse(it, CAMPAIGN.id)
        self.assert_in_time(it, "defer", "followup")  # never "thinking…" for good
        self.assertIn("couldn't load the names", it.followup.send.await_args.args[0])

    async def test_a_menu_that_breaks_after_answering_says_so(self) -> None:
        view = name_lists.Browse(CAMPAIGN.id, _names(), "npc")
        it = self.it()
        await it.response.defer()
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
            await view.on_error(it, RuntimeError("boom"), view.children[0])
        self.assertEqual(it.followup.send.await_args.args[0], dmbot_commands.TRY_AGAIN)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])  # never public

    async def test_every_form_and_question_says_so_too(self) -> None:
        views: list[Any] = [
            name_card.PickForm(CAMPAIGN.id, BELL, name_card.SAME, "Same as"),
            name_card.FindForm(CAMPAIGN.id),
            *self.saving_forms(),
            name_lists.KindQuestions(CAMPAIGN.id, [("wizard", [BELL])]),
            name_lists.NearQuestions(CAMPAIGN.id, []),
            dmbot_commands.Welcome(),  # any menu in the bot, not only the names ones
        ]
        for view in views:
            with self.subTest(type(view).__name__):
                it = self.it()  # broke before answering: a private message, not a followup
                with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
                    if isinstance(view, discord.ui.Modal):
                        await view.on_error(it, RuntimeError("boom"))
                    else:
                        await view.on_error(it, RuntimeError("boom"), MagicMock())
                self.assertEqual(it.response.sent_kw["text"], dmbot_commands.TRY_AGAIN)
                self.assertTrue(it.response.sent_kw["ephemeral"])

    @staticmethod
    def saving_forms() -> list[discord.ui.Modal]:
        """The forms that answer first, then save (#595 review)."""
        return [
            name_card.FixSpellingForm(CAMPAIGN.id, BELL, "Belleros"),
            name_card.AnotherNameForm(CAMPAIGN.id, BELL, "Belleros", secrets=True),
            names.AddNameForm(CAMPAIGN.id, secrets=True),
            names.CharacterForm(CAMPAIGN.id, 5, "Ann"),
        ]

    async def test_a_form_that_breaks_after_answering_says_so(self) -> None:
        # Answered first, then the save broke: without on_error the form just closes.
        for form in self.saving_forms():
            with self.subTest(type(form).__name__):
                it = self.it()
                await it.response.defer()
                with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
                    await form.on_error(it, ConnectionError("database away"))
                self.assertEqual(it.followup.send.await_args.args[0], dmbot_commands.TRY_AGAIN)
                self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])

    async def test_a_slash_command_that_breaks_after_answering_says_so(self) -> None:
        self.bot.memory.entities = AsyncMock(side_effect=ConnectionError("database away"))
        it = self.it(discord.InteractionType.application_command)
        with self.assertRaises(ConnectionError) as caught:
            await names.show_home(it, CAMPAIGN.id)  # /dmbot names
        error = discord.app_commands.CommandInvokeError(MagicMock(), caught.exception)
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR") as logs:
            await DMBotTree.on_error(MagicMock(), it, error)
        record = logs.records[0]
        assert record.exc_info is not None
        self.assertIs(record.exc_info[1], caught.exception)  # the cause, not the wrapper
        self.assertEqual(it.followup.send.await_args.args[0], dmbot_commands.TRY_AGAIN)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])

    async def test_any_other_command_error_says_so_privately(self) -> None:
        it = self.it(discord.InteractionType.application_command)
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR") as logs:
            await DMBotTree.on_error(MagicMock(), it, discord.app_commands.CheckFailure("x"))
        self.assertIn("CheckFailure", "\n".join(logs.output))
        self.assertEqual(it.response.sent_kw["text"], dmbot_commands.TRY_AGAIN)
        self.assertTrue(it.response.sent_kw["ephemeral"])

    async def test_broken_suggestions_are_only_logged(self) -> None:
        it = self.it(discord.InteractionType.autocomplete)
        with self.assertLogs("dmbot.bot", "WARNING"):
            await DMBotTree.on_error(MagicMock(), it, discord.app_commands.CommandNotFound("x", []))
        self.assertEqual(self.clock.calls, [])  # nothing answered: Discord would refuse

    async def test_a_slash_command_is_always_answered_privately(self) -> None:
        it = self.it(discord.InteractionType.application_command)
        await name_card.show_card(it, CAMPAIGN.id, BELL, replace=True)  # no message to edit
        self.assertEqual(it.response.defer_kw, NEW)

    async def test_kind_question_answers_before_saving(self) -> None:
        questions = name_lists.KindQuestions(CAMPAIGN.id, [("wizard", [BELL, ULF])])
        (select,) = questions.children
        assert isinstance(select, name_lists.KindSelect)
        select._values = ["npc"]
        it = self.it()
        await questions.picked(select, it)
        self.assert_in_time(it, "edit_message", "edit_original")
        assert it.response.content is not None
        self.assertTrue(it.response.content.endswith(name_lists.SAVING))  # menus away
        final = it.edit_original_response.await_args.kwargs
        self.assertIn("2 names set to", final["content"])
        self.assertIsNone(final["view"])  # nothing left to ask

    async def pick_wizard(self, it: Any) -> name_lists.KindQuestions:
        questions = name_lists.KindQuestions(CAMPAIGN.id, [("wizard", [BELL]), ("orc", [ULF])])
        select = questions.children[0]
        assert isinstance(select, name_lists.KindSelect)
        select._values = ["npc"]
        await questions.picked(select, it)
        return questions

    async def test_a_kind_question_with_no_campaign_puts_the_menu_back(self) -> None:
        self.bot.campaigns.get = AsyncMock(return_value=None)
        it = self.it()
        questions = await self.pick_wizard(it)
        self.assertEqual(it.followup.send.await_args.args[0], logic.NO_CAMPAIGN_ACCESS)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])
        self.assertIs(it.edit_original_response.await_args.kwargs["view"], questions)

    async def test_a_refused_kind_question_says_why_and_puts_the_menu_back(self) -> None:
        self.bot.memory.confirm_kinds = AsyncMock(side_effect=MemoryRuleError("No."))
        it = self.it()
        questions = await self.pick_wizard(it)
        self.assertEqual(it.followup.send.await_args.args[0], "Couldn't change them. No.")
        self.assertIs(it.edit_original_response.await_args.kwargs["view"], questions)

    async def test_answers_without_a_summary_start_at_the_first_line(self) -> None:
        it = self.it()
        it.message = None  # nothing above the questions
        await self.pick_wizard(it)
        content = it.edit_original_response.await_args.kwargs["content"]
        self.assertTrue(content.startswith("✅ Every **wizard**"))

    async def test_a_failed_redraw_never_hides_why_the_save_failed(self) -> None:
        self.bot.memory.confirm_kinds = AsyncMock(side_effect=RuntimeError("database away"))
        it = self.it()
        gone = discord.HTTPException(MagicMock(status=404), "gone")
        it.edit_original_response = AsyncMock(side_effect=gone)
        with self.assertRaises(RuntimeError), self.assertLogs("dmbot.ui.name_lists", "WARNING"):
            await self.pick_wizard(it)  # on to on_error with the real cause

    async def test_a_failed_redraw_after_saving_is_logged(self) -> None:
        it = self.it()
        gone = discord.HTTPException(MagicMock(status=404), "gone")
        it.edit_original_response = AsyncMock(side_effect=gone)
        with self.assertLogs("dmbot.ui.name_lists", "WARNING") as logs:
            await self.pick_wizard(it)  # saved: no "Try again", but never silent
        self.assertIn("Couldn't redraw", logs.output[0])

    async def test_a_kind_question_that_cant_answer_says_so_and_can_be_picked_again(
        self,
    ) -> None:
        # #698: the "Saving…" edit itself fails (Discord gave up): nothing is saved, the
        # menu isn't stuck "saving", and the error reaches on_error, which says so.
        it = self.it()
        expired = discord.NotFound(MagicMock(status=404), "Unknown interaction")
        it.response.edit_message = AsyncMock(side_effect=expired)
        questions = name_lists.KindQuestions(CAMPAIGN.id, [("wizard", [BELL])])
        (select,) = questions.children
        assert isinstance(select, name_lists.KindSelect)
        select._values = ["npc"]
        with self.assertRaises(discord.NotFound):
            await questions.picked(select, it)
        self.assertFalse(questions.busy)
        self.bot.memory.confirm_kinds.assert_not_awaited()
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
            await questions.on_error(it, expired, select)  # nothing raises out
        it = self.it()
        await questions.picked(select, it)  # picked again: saves
        self.bot.memory.confirm_kinds.assert_awaited_once()

    async def test_saying_it_broke_never_breaks_too(self) -> None:
        # #698: after a stall the token can be gone, so even "Something broke" fails to
        # send. That's swallowed, and the first error is still logged.
        for answered in (False, True):
            with self.subTest(answered=answered):
                it = self.it()
                gone = discord.HTTPException(MagicMock(status=401), "Invalid Webhook Token")
                it.response.send_message = AsyncMock(side_effect=gone)
                it.followup.send = AsyncMock(side_effect=gone)
                if answered:
                    await it.response.defer()
                with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR") as logs:
                    await dmbot_commands._failed(it, RuntimeError("database away"))
                record = logs.records[0]
                assert record.exc_info is not None
                self.assertIsInstance(record.exc_info[1], RuntimeError)
                sender = it.followup.send if answered else it.response.send_message
                sender.assert_awaited_once()

    async def test_kind_questions_keep_every_answer(self) -> None:
        asked = [("wizard", [BELL]), ("goblin", [ULF])]
        questions = name_lists.KindQuestions(CAMPAIGN.id, asked)
        first, second = questions.children
        assert isinstance(first, name_lists.KindSelect)
        assert isinstance(second, name_lists.KindSelect)
        first._values, second._values = ["npc"], ["creature"]
        self.gate = asyncio.Event()
        saving = asyncio.create_task(questions.picked(first, self.it()))
        await asyncio.sleep(0)
        it = self.it()  # picked again while saving: nothing happens twice
        await asyncio.wait_for(questions.picked(second, it), 1)  # never waits on the first
        self.assertEqual(self.clock.calls, ["defer"])
        self.gate.set()
        await saving
        self.gate = None
        it = self.it()
        await questions.picked(second, it)
        content = it.edit_original_response.await_args.kwargs["content"]
        self.assertTrue(content.startswith("📥 Added 2 names."))
        self.assertIn("Every **wizard**", content)  # the first answer is still there
        self.assertIn("Every **goblin**", content)


if __name__ == "__main__":
    unittest.main()
