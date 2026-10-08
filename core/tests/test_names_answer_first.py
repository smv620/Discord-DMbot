"""Names menus answer Discord before loading names (#537), with a fake clock: Discord
gives up 3 seconds after a press, and a big campaign's names can take longer to load."""

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns.models import Campaign
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, Alias, Entity, name_key
from dmbot.memory.sounds import sound_codes
from dmbot.ui import dmbot_commands, name_card, name_lists, names

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

    async def send_message(self, *_: Any, **__: Any) -> None:
        self._once("send_message")

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

    def it(self) -> Any:
        """A new press, on a fresh clock."""
        self.clock.now, self.clock.answered_at, self.clock.calls = 0.0, None, []
        user = MagicMock(spec=discord.Member)
        user.id = DM
        user.guild_permissions = discord.Permissions.none()
        return SimpleNamespace(
            client=self.bot,
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
