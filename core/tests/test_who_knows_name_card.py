"""Who knows an other name (#2935, slice C2) on the name card: the three answers on one name's
page, in the same words as a connection's, and the checks every press makes. Invented names."""

from __future__ import annotations

from typing import Any, cast

from dmbot.memory.models import CONFIRMED
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests import test_memory_names as base

GUILD, DM, MANAGER = base.GUILD, base.DM, base.MANAGER


class NameWho(base.NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.bell = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def alias(self, text: str = "Bell") -> Any:
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.bell.id, text, kind="nickname", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        self.fresh()
        found = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=self.bell.id, include_secret=True
        )
        return next(a for a in found if a.text == text)

    async def pc(self, name: str, user: int) -> Any:
        made = await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, made.id, "player_character", source="dm", played_by=user
        )
        self.fresh()
        return made

    async def who_page(self, alias: Any, who: int = DM) -> tuple[str, Any]:
        view = name_card.OneName(self.campaign.id, self.bell.id, alias, secrets=True)
        it = self.it(who)
        await view._who(it)
        return cast("tuple[str, Any]", it.response.edited[0])

    async def current(self, text: str = "Bell") -> Any:
        found = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=self.bell.id, include_secret=True
        )
        return next(a for a in found if a.text == text)

    async def test_the_button_replaces_keep_it_secret_and_says_where_it_stands(self) -> None:
        alias = await self.alias()
        view = name_card.OneName(self.campaign.id, self.bell.id, alias, secrets=True)
        labels = [getattr(c, "label", "") for c in view.children]
        self.assertIn(name_card.WHO_BUTTON, labels)
        self.assertFalse([x for x in labels if "secret" in x.lower()])
        text, _ = await self.who_page(alias)
        self.assertIn("Players can see this name.", text)

    async def test_only_the_dm_then_everyone_are_the_old_switches(self) -> None:
        alias = await self.alias()
        _, view = await self.who_page(alias)
        it = self.it()
        await view._only_me(it)
        self.assertIn("is secret now: only the DM knows it", it.response.edited[0][0])
        self.assertTrue((await self.current()).secret)
        _, again = await self.who_page(await self.current())
        it = self.it()
        await again._everyone(it)
        self.assertIn("isn't secret any more", it.response.edited[0][0])
        self.assertFalse((await self.current()).secret)

    async def test_some_characters_know_a_secret_name_and_the_page_says_who(self) -> None:
        alias = await self.alias()
        mara = await self.pc("Mara", 1001)
        await self.pc("Tavin", 1002)
        _, view = await self.who_page(alias)
        self.assertEqual(sorted(o.label for o in view.pick.options), ["Mara", "Tavin"])
        view.pick._values = [mara.id]
        await view._picked(self.it())
        it = self.it()
        await view._some(it)
        self.assertIn("only Mara knows it now (and the DM)", it.response.edited[0][0])
        now = await self.current()
        self.assertTrue(now.secret)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD, self.campaign.id, [now.id]), {now.id: [mara.id]}
        )
        text, _ = await self.who_page(now)
        self.assertIn("Only Mara knows it (and the DM)", text)

    async def test_everyone_lets_go_of_who_knew_it(self) -> None:
        alias = await self.alias()
        mara = await self.pc("Mara", 1001)
        _, view = await self.who_page(alias)
        view.pick._values = [mara.id]
        await view._picked(self.it())
        await view._some(self.it())
        _, again = await self.who_page(await self.current())
        await again._everyone(self.it())
        now = await self.current()
        self.assertFalse(now.secret)
        self.assertEqual(await self.memory.alias_knowers(GUILD, self.campaign.id, [now.id]), {})

    async def test_a_bad_pick_changes_nothing_not_even_the_hiding(self) -> None:
        alias = await self.alias()
        npc = await ui.save_name(self.memory, self.campaign, "Old Smith", "npc", [], [])
        _, view = await self.who_page(alias)
        view.picked = [npc.id]  # forged: not a player character
        it = self.it()
        await view._some(it)
        self.assertIn("Couldn't change that", str(it.response.sent or it.followup.send.await_args))
        self.assertFalse((await self.current()).secret)

    async def test_a_double_press_and_a_stale_page_write_nothing(self) -> None:
        alias = await self.alias()
        _, view = await self.who_page(alias)
        await view._only_me(self.it())
        again = self.it()
        await view._only_me(again)  # the same page, pressed twice
        self.assertIn(
            "Already like that", str(again.response.sent or again.followup.send.await_args)
        )
        self.assertTrue((await self.current()).secret)

    async def test_a_manager_cannot_press_it(self) -> None:
        alias = await self.alias()
        _, view = await self.who_page(alias)
        await view._only_me(self.it(MANAGER))
        self.assertFalse((await self.current()).secret)
        opened = self.it(MANAGER)
        await name_card.OneName(self.campaign.id, self.bell.id, alias, secrets=False)._who(opened)
        self.assertIn("Only the campaign's DMs", opened.response.sent[0][0])
