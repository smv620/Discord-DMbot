"""The DM's own "next to" and "travel to" links (#4173 slice 6d, the route menus; Supervisor's yes
and the ux-critic's words): the follow-up menu finishes a sentence that starts with the card's
place, its hints stay within Discord's 100 characters, a one-way word is saved the right way
round whichever place's id sorts first, and a person is never offered as the other place.
Through PlaceWords._picked, choose_other and the real store. Invented and published names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import next_words, route_words
from dmbot.memory.lookup import CampaignLookup
from dmbot.ui import name_card
from dmbot.ui import place_links as pl
from tests.test_memory_names import GUILD, PLAYER, NamesTest


class TheWords(unittest.TestCase):
    def test_the_search_asks_from_the_cards_place(self) -> None:
        self.assertEqual(pl.asked("Targos", pl.NEXT_TO), "Targos is next to…")
        self.assertEqual(pl.asked("Targos", pl.TRAVEL_TO), "Travel from Targos to…")
        self.assertEqual(name_card._asked("Targos", pl.TRAVEL_TO), "Travel from Targos to…")

    def test_every_hint_fits_with_two_long_names(self) -> None:
        for how in (pl.NEXT_TO, pl.TRAVEL_TO):
            for option in pl.options(how, "A" * 60, "B" * 60):
                with self.subTest(how=how, label=option.label):
                    self.assertLessEqual(len(option.description or ""), 100)
                    self.assertLessEqual(len(option.label), 25)

    def test_a_one_way_hint_starts_with_the_cards_place(self) -> None:
        hints = {o.label: o.description for o in pl.options(pl.NEXT_TO, "Targos", "Maer Dualdon")}
        self.assertEqual(hints["On the shore (lake)"], "Targos is on the shore of Maer Dualdon")
        self.assertEqual(hints["Next to"], "Targos and Maer Dualdon are next to each other")
        ways = {o.label: o.value for o in pl.options(pl.TRAVEL_TO, "a", "b")}
        self.assertEqual((ways["By road"], ways["Not sure"]), ("road", pl.NOT_SAID))


class TheWebsitesWords(unittest.TestCase):
    def test_the_cards_place_words_read_back_as_their_kind(self) -> None:
        from dmbot.memory.link_words import predicate_for

        self.assertEqual(predicate_for("is on the shore of"), ("next_to", "on the shore of"))
        self.assertEqual(predicate_for("borders"), ("next_to", "borders"))
        self.assertEqual(predicate_for("is connected by road to"), ("route_to", "road"))
        self.assertEqual(predicate_for("is linked by a portal to"), ("route_to", "portal"))
        self.assertEqual(predicate_for("is on"), ("associated_with", "is on"))  # (too short)
        self.assertEqual(predicate_for("serves"), ("associated_with", "serves"))


class TheFlow(NamesTest):
    async def place(self, name: str) -> Any:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="place", status="confirmed", source="dm"
        )
        return made.value

    async def pick(
        self, how: str, first: Any, other: Any, value: str, user: int | None = None, **kw: Any
    ) -> Any:
        menu = pl.PlaceWords(
            self.campaign.id, first.id, other.id, how, (first.name, other.name), **kw
        )
        menu.pick = SimpleNamespace(values=[value])  # type: ignore[assignment]
        it = self.it() if user is None else self.it(user)
        await menu._picked(it)
        return it

    async def card_words(self, card_of: Any) -> str:
        names = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))
        rels = await self.memory.relations(GUILD, self.campaign.id, entity_id=card_of.id)
        (row,) = name_card._rows(names, card_of.id, rels, True)
        return f"{row.words} {row.other}"

    async def test_on_the_shore_is_saved_from_the_cards_place_either_id_order(self) -> None:
        for n in range(2):
            a, b = await self.place(f"Targos {n}"), await self.place(f"Maer Dualdon {n}")
            town, lake = (a, b) if (a.id < b.id) == (n == 0) else (b, a)
            await self.pick(pl.NEXT_TO, town, lake, "on the shore of")
            self.assertEqual(await self.card_words(town), f"is on the shore of {lake.name}")
            hill = await self.place(f"Kelvin's Cairn {n}")
            keep, peak = (town, hill) if (town.id < hill.id) == (n == 0) else (hill, town)
            await self.pick(pl.NEXT_TO, keep, peak, "at the foot of")
            (rel,) = [
                r
                for r in await self.memory.relations(GUILD, self.campaign.id, entity_id=keep.id)
                if r.detail == "at the foot of"
            ]
            self.assertEqual((rel.subject_id, rel.object_id), (keep.id, peak.id))

    async def test_a_way_is_saved_with_its_means_and_not_sure_with_none(self) -> None:
        targos, bryn = await self.place("Targos"), await self.place("Bryn Shander")
        await self.pick(pl.TRAVEL_TO, targos, bryn, "road")
        self.assertEqual(await self.card_words(targos), "is connected by road to Bryn Shander")
        lone = await self.place("Lonelywood")
        await self.pick(pl.TRAVEL_TO, lone, targos, pl.NOT_SAID)
        (rel,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=lone.id)
        self.assertEqual((rel.predicate, rel.detail, rel.status), ("route_to", "", "confirmed"))

    async def test_a_person_is_never_the_other_place(self) -> None:
        targos = await self.place("Targos")
        mira = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Mira", type="npc", status="confirmed", source="dm"
        )
        it = self.it()
        view = name_card.PickOther(self.campaign.id, targos.id, pl.TRAVEL_TO, "Targos", [])
        await name_card.choose_other(
            it, view, mira.value.id, self.campaign.id, targos.id, pl.TRAVEL_TO, secret=False
        )
        said = " ".join(text for text, _ in it.response.sent)
        self.assertIn("isn't a place", said)
        self.assertFalse(view.is_finished())  # (the list still works: pick another)
        self.assertEqual(
            await self.memory.relations(GUILD, self.campaign.id, entity_id=targos.id), []
        )

    async def test_every_choice_saves_its_own_word(self) -> None:
        for how, choices, read in (
            (pl.NEXT_TO, pl.NEXT_CHOICES, next_words.word_of),
            (pl.TRAVEL_TO, pl.TRAVEL_CHOICES, route_words.means_of),
        ):
            for n, (label, detail) in enumerate(choices):
                with self.subTest(how=how, label=label):
                    a, b = await self.place(f"{how} A{n}"), await self.place(f"{how} B{n}")
                    await self.pick(how, a, b, detail or pl.NOT_SAID)
                    (rel,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=a.id)
                    self.assertEqual((rel.predicate, rel.detail), (how, detail))
                    self.assertEqual(read(detail), detail)

    async def test_new_words_change_the_link_in_one_undo(self) -> None:
        # (#4284: two places already next to each other get new words, never a second link)
        for n in range(2):
            a, b = await self.place(f"Targos {n}"), await self.place(f"Maer Dualdon {n}")
            town, lake = (a, b) if (a.id < b.id) == (n == 0) else (b, a)
            await self.pick(pl.NEXT_TO, town, lake, next_words.BORDERS)
            it = await self.pick(pl.NEXT_TO, town, lake, "on the shore of")
            note = str(it.response.edited[-1][0]) if it.response.edited else ""
            self.assertIn("🧭 Changed:", note)
            rels = await self.memory.relations(GUILD, self.campaign.id, entity_id=town.id)
            self.assertEqual([(r.predicate, r.detail) for r in rels],
                             [(pl.NEXT_TO, "on the shore of")])  # fmt: skip
            self.assertEqual(await self.card_words(town), f"is on the shore of {lake.name}")
            async with self.db.guild(GUILD) as conn:
                cur = await conn.execute(
                    "SELECT max(batch) AS b FROM memory_changes WHERE campaign_id = %s",
                    (self.campaign.id,),
                )
                row = await cur.fetchone()
            assert row is not None
            await self.memory.undo(GUILD, self.campaign.id, int(row["b"]), source="dm")
            rels = await self.memory.relations(GUILD, self.campaign.id, entity_id=town.id)
            self.assertEqual([r.detail for r in rels], [next_words.BORDERS])  # (the old words)

    async def test_the_same_words_again_change_nothing(self) -> None:
        targos, lake = await self.place("Targos"), await self.place("Maer Dualdon")
        await self.pick(pl.NEXT_TO, targos, lake, "on the shore of")
        await self.pick(pl.NEXT_TO, targos, lake, "on the shore of")
        rels = await self.memory.relations(GUILD, self.campaign.id, entity_id=targos.id)
        self.assertEqual(len(rels), 1)

    async def test_only_the_dm_saves_one(self) -> None:
        a, b = await self.place("Targos"), await self.place("Bryn Shander")
        it = await self.pick(pl.TRAVEL_TO, a, b, "road", user=PLAYER)
        # (refused before it is saved: by the card's own check, or by ONLY_DM in `connect`)
        said = " ".join(text for text, _ in it.response.sent)
        self.assertTrue("DM" in said, said)
        self.assertEqual(await self.memory.relations(GUILD, self.campaign.id, entity_id=a.id), [])

    async def test_a_place_found_by_a_secret_name_is_linked_in_secret(self) -> None:
        a, b = await self.place("Targos"), await self.place("Bryn Shander")
        await self.pick(pl.NEXT_TO, a, b, "", secret=True, forced=True)
        (rel,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=a.id, include_secret=True
        )
        self.assertTrue(rel.secret)
