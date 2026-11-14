"""The name card's way to write the players' line (#2967): a DM-only button and a short form, the
DM sees the line on the card, a manager who is not a DM does not, and the store's limits hold.
Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.memory.lookup import LookupCache
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

LINE = "carries a lantern that glows green near monsters"


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.bell = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", ["Bell"], [])

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def sent(self, user: int = DM) -> tuple[str, Any]:
        self.fresh()
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        text, kw = it.response.sent[0][0], it.response.sent[0][1]
        return str(text), kw["view"]

    async def labels(self, user: int = DM) -> list[str]:
        _text, view = await self.sent(user)
        return [str(getattr(c, "label", "")) for c in view.children]

    async def submit(self, typed: str, user: int = DM) -> Any:
        form = name_card.PlayersLineForm(self.campaign.id, self.bell.id, "Belleros", None)
        form.line = SimpleNamespace(value=typed)  # type: ignore[assignment]
        it = self.it(user)
        await form.on_submit(it)
        return it

    async def stored(self) -> str | None:
        entity = await self.memory.entity(GUILD, self.campaign.id, self.bell.id)
        assert entity is not None
        return entity.players_text

    async def test_the_button_is_for_dms_only(self) -> None:
        self.assertIn(name_card.PLAYERS_LINE_BUTTON, await self.labels())
        self.assertNotIn(name_card.PLAYERS_LINE_BUTTON, await self.labels(MANAGER))

    async def test_the_form_saves_the_line_and_the_dm_sees_it_on_the_card(self) -> None:
        it = await self.submit(LINE)
        self.assertIn("Players will read this in recaps and answers", it.response.edited[0][0])
        self.assertEqual(await self.stored(), LINE)
        dm_text, _ = await self.sent()
        self.assertIn("Line for the players", dm_text)
        self.assertIn("glows green", dm_text)
        manager_text, _ = await self.sent(MANAGER)
        self.assertNotIn("glows green", manager_text)  # a manager who is not a DM is not shown it

    async def test_blank_clears_it(self) -> None:
        await self.submit(LINE)
        it = await self.submit("   ")
        self.assertIn("Your own note is unchanged", it.response.edited[0][0])
        self.assertIsNone(await self.stored())

    async def test_a_manager_who_is_not_a_dm_cannot_save_it(self) -> None:
        it = await self.submit(LINE, user=MANAGER)
        self.assertIn("Only a DM", it.response.sent[0][0])
        self.assertIsNone(await self.stored())

    async def test_the_button_press_by_a_manager_is_refused_and_a_dm_gets_the_form(self) -> None:
        card = name_card.NameCard(self.campaign.id, self.bell.id, players_line=True)
        press = self.it(MANAGER)
        await card._players_line(press)
        self.assertIn("Only a DM", press.response.sent[0][0])
        dm_press = self.it()
        await card._players_line(dm_press)
        self.assertIsInstance(dm_press.response.modal, name_card.PlayersLineForm)

    async def test_a_line_over_the_limit_is_refused_not_cut(self) -> None:
        it = await self.submit("x" * 121)
        self.assertIn("Couldn't save it", it.response.sent[0][0])
        self.assertIsNone(await self.stored())
