"""Telling one character a secret other name is its entry, on the name card's "Who knows this?"
page (#3542): the choice, the line once told, the one confirmation to take it back, DM only on every
press, and each refusal worded. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.ui import name_card, told_name
from tests.test_who_knows_name_card import DM, GUILD, MANAGER, NameWho


class Told(NameWho):
    async def secret_held_by_mara(self) -> tuple[Any, Any]:
        """Belleros's other name "Bell", secret, held by the player character Mara."""
        alias = await self.alias()
        mara = await self.pc("Mara", 1001)
        _, view = await self.who_page(alias)
        view.pick._values = [mara.id]
        await view._picked(self.it())
        await view._some(self.it())
        return await self.current(), mara

    async def page(self, who: int = DM, note: str = "") -> tuple[str, Any]:
        it = self.it(who)
        alias = await self.current()
        await name_card.show_who_page(it, self.campaign.id, self.bell.id, alias.id, note=note)
        return it.response.edited[0]  # type: ignore[no-any-return]

    def labels(self, view: Any) -> list[str]:
        return [o.label for c in view.children for o in getattr(c, "options", [])]

    def said(self, it: Any) -> str:
        """What the press answered with, whether it was a first reply or a follow-up."""
        return str(it.response.sent or it.followup.send.await_args)

    async def told(self, alias: Any) -> list[str]:
        return await self.memory.told_characters(GUILD, self.campaign.id, alias.id)

    async def test_a_holder_who_was_not_told_is_offered_in_the_exact_words(self) -> None:
        await self.secret_held_by_mara()
        text, view = await self.page()
        self.assertIn("Mara now knows Bell is Belleros", self.labels(view))
        self.assertNotIn("was told", text)

    async def test_telling_records_it_and_the_page_then_says_so_and_offers_take_back(self) -> None:
        alias, mara = await self.secret_held_by_mara()
        it = self.it()
        await told_name.tell(it, self.campaign.id, self.bell.id, alias.id, mara.id)
        self.assertEqual(await self.told(alias), [mara.id])
        text, view = it.response.edited[0]
        self.assertIn("Mara now knows Bell is Belleros", text)
        self.assertIn("Mara was told that Bell is Belleros", text)
        labels = self.labels(view)
        self.assertIn("Mara was told that Bell is Belleros", labels)  # the take-back choice
        self.assertNotIn("Mara now knows Bell is Belleros", labels)  # not offered twice

    async def test_taking_back_asks_once_in_plain_words_and_keep_it_changes_nothing(self) -> None:
        alias, mara = await self.secret_held_by_mara()
        await told_name.tell(self.it(), self.campaign.id, self.bell.id, alias.id, mara.id)
        ask = self.it()
        await told_name.ask_take_back(ask, self.campaign.id, self.bell.id, alias.id, mara.id)
        text, confirm = ask.response.edited[0]
        self.assertEqual(text, "Take back? Mara will see Bell and Belleros as two people again.")
        keep = self.it()
        await confirm._no(keep)
        self.assertEqual(await self.told(alias), [mara.id])  # still told
        yes = self.it()
        again = self.it()
        await told_name.ask_take_back(again, self.campaign.id, self.bell.id, alias.id, mara.id)
        await again.response.edited[0][1]._yes(yes)
        self.assertEqual(await self.told(alias), [])
        self.assertIn(
            "Taken back. Mara will see Bell and Belleros as two people",
            str(yes.response.edited[0][0]),
        )

    async def test_a_manager_who_is_not_a_dm_can_do_neither_and_nothing_changes(self) -> None:
        alias, mara = await self.secret_held_by_mara()
        refused = self.it(MANAGER)
        await told_name.tell(refused, self.campaign.id, self.bell.id, alias.id, mara.id)
        # (a secret name is not even visible to someone who is not a DM)
        self.assertIn("isn't there any more", self.said(refused))
        self.assertEqual(await self.told(alias), [])
        await told_name.tell(self.it(), self.campaign.id, self.bell.id, alias.id, mara.id)
        again = self.it(MANAGER)
        await told_name.take_back(again, self.campaign.id, self.bell.id, alias.id, mara.id)
        self.assertEqual(await self.told(alias), [mara.id])
        opened = self.it(MANAGER)
        await name_card.show_who_page(opened, self.campaign.id, self.bell.id, alias.id)
        self.assertEqual(opened.response.edited, [])  # no page for a non-DM

    async def test_each_refusal_is_worded_plainly(self) -> None:
        tavin = await self.pc("Tavin", 1002)  # made first: a character who does not hold the name
        alias, mara = await self.secret_held_by_mara()
        no_hold = self.it()
        await told_name.tell(no_hold, self.campaign.id, self.bell.id, alias.id, tavin.id)
        self.assertIn("does not know that name", self.said(no_hold))
        nothing = self.it()
        await told_name.ask_take_back(nothing, self.campaign.id, self.bell.id, alias.id, mara.id)
        self.assertIn("not told this", self.said(nothing))
        # a name that is not secret any more cannot be told
        await self.memory.update_alias(GUILD, self.campaign.id, alias.id, source="dm", secret=False)
        public = self.it()
        await told_name.tell(public, self.campaign.id, self.bell.id, alias.id, mara.id)
        self.assertIn("secret name", self.said(public))
        self.assertEqual(await self.told(alias), [])

    async def test_the_page_is_unchanged_when_nothing_is_held_or_told(self) -> None:
        await self.alias()
        text, view = await self.page()
        self.assertEqual(self.labels(view).count("Mara now knows Bell is Belleros"), 0)
        self.assertNotIn("was told", text)
