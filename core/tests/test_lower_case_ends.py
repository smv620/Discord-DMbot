"""A kept item's or place's own name in small letters is a connection end in a later upload (#4923;
Supervisor's ruling, 2026-11-21): "wolf figurine" and "old mill" are names, while people, groups,
other spellings, two entries with one spelling, and names starting "the", "a" or "an" keep #3431's
rule. Invented names and text."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.memory import background as bg
from dmbot.memory import descriptions
from dmbot.memory.models import CONFIRMED, NewName
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import name
from tests.test_memory_names import DM, GUILD, NamesTest

SOLD = "Claude sold the wolf figurine to Belleros"
KNOWN = {"belleros": "Belleros", "wolf figurine": "wolf figurine", "old mill": "old mill"}
LATER = "Much later, Claude sold the wolf figurine to Belleros for 75 gold."


class TheRule(unittest.TestCase):
    """`_kept_thing`, with a stand-in lookup: each limit of the ruling on its own."""

    def lookup(self, *entries: tuple[str, str, str]) -> Any:
        # (entity id, its main name, its kind); every spelling's key is its text, small letters
        names = [SimpleNamespace(entity_id=i, text=t, key=t.casefold(), person=False)
                 for i, t, _ in entries]  # fmt: skip
        by_key: dict[str, list[Any]] = {}
        for n in names:
            by_key.setdefault(n.key, []).append(n)
        entities = {i: SimpleNamespace(name=t, type=k) for i, t, k in entries}
        return SimpleNamespace(entities=entities, by_key=by_key), names

    def kept(self, *entries: tuple[str, str, str], at: int = 0, main: str = "") -> bool:
        lookup, names = self.lookup(*entries)
        e = names[at]
        if main:
            lookup.entities[e.entity_id] = SimpleNamespace(name=main, type=entries[at][2])
        # (a campaign's own kinds sit under the core ones: "relic" is an item, "ruin" a place)
        parents = {"relic": "item", "ruin": "place"}
        onto = SimpleNamespace(is_a=lambda kind, root: root in (kind, parents.get(kind)))
        return ui_bg._kept_thing(cast(Any, lookup), cast(Any, onto), cast(Any, e))

    def test_a_kept_item_or_place_in_small_letters(self) -> None:
        self.assertTrue(self.kept(("1", "wolf figurine", "item")))
        self.assertTrue(self.kept(("1", "old mill", "place")))

    def test_a_campaigns_own_kind_under_item_or_place(self) -> None:
        self.assertTrue(self.kept(("1", "moon chalice", "relic")))
        self.assertTrue(self.kept(("1", "sunken keep", "ruin")))

    def test_a_near_twin_spelling_on_another_entry_is_a_question(self) -> None:
        # ("Wolf-Figurine" and "wolf figurine" are one name key: two entries, never a guess)
        lookup, names = self.lookup(("1", "wolf figurine", "item"), ("2", "Wolf-Figurine", "item"))
        names[1].key = names[0].key
        lookup.by_key = {names[0].key: names}
        onto = SimpleNamespace(is_a=lambda kind, root: kind == root)
        self.assertFalse(ui_bg._kept_thing(cast(Any, lookup), cast(Any, onto), cast(Any, names[0])))

    def test_people_and_groups_keep_the_old_rule(self) -> None:
        self.assertFalse(self.kept(("1", "old tom", "character")))
        self.assertFalse(self.kept(("1", "night watch", "faction")))

    def test_a_name_starting_like_a_description_stays_one(self) -> None:
        for text in ("the old mill", "a dagger", "an amulet", "The cave", "my sword", "that cave",
                     "their camp", "those ruins"):  # fmt: skip
            with self.subTest(text):
                self.assertFalse(self.kept(("1", text, "place")))
                self.assertFalse(descriptions.kept_thing_alone(text))

    def test_two_entries_with_one_spelling_are_a_question(self) -> None:
        self.assertFalse(self.kept(("1", "wolf figurine", "item"), ("2", "wolf figurine", "item")))

    def test_another_spelling_is_never_one(self) -> None:
        self.assertFalse(self.kept(("1", "figurine", "item"), main="Wolf Figurine of Kel"))

    def test_a_small_letter_end_matches_a_name_only_whole_when_asked(self) -> None:
        from dmbot.memory.end_match import Ends

        ends = Ends({"ring of frostmere": "Ring of Frostmere"}, ["Ring of Frostmere"])
        self.assertEqual(ends.match("Frostmere", small_whole_only=True).name, "Ring of Frostmere")
        self.assertIsNone(ends.match("a ring", small_whole_only=True).name)  # (#4991)
        self.assertEqual(ends.match("a ring").name, "Ring of Frostmere")  # (the clash check's way)


class OnlyWhole(unittest.TestCase):
    """`keep_connections`' `whole_only` (the reviewer's finding on #4923): a word of a small-letter
    name ("wolf", "the mill") never stands for it, and its quote must hold it whole."""

    def kept(self, b: str, quote: str) -> list[Any]:
        made = bg.Connection("Belleros", "owns", b, quote)
        return bg.keep_connections([[made]], [], KNOWN, whole_only={"wolf figurine", "old mill"})

    def test_a_word_of_it_is_not_the_name(self) -> None:
        for end, quote in (
            ("wolf", "Belleros owns the wolf"),
            ("figurine", "Belleros owns the figurine"),
            ("the figurine", "Belleros owns the figurine"),
            ("the mill", "Belleros owns the mill"),
        ):
            with self.subTest(end):
                self.assertEqual(self.kept(end, quote), [])

    def test_the_quote_must_hold_it_whole(self) -> None:
        self.assertEqual(self.kept("wolf figurine", "Belleros owns the wolf"), [])

    def test_the_whole_name_is_kept_with_its_article(self) -> None:
        got = self.kept("the wolf figurine", "Belleros owns the wolf figurine")
        self.assertEqual([c.b for c in got], ["wolf figurine"])


class TheLaterUpload(NamesTest):
    """The case #4170 found: the item kept from one upload, its sale read from the next."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        from dmbot.memory.backstory_links import BackstoryLinks

        self.bot.backstory_links = BackstoryLinks(self.db)

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self, item: str, kind: str = "item") -> Any:
        await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Claude Ambere", "npc", CONFIRMED), NewName("Belleros", "npc", CONFIRMED),
             NewName(item, kind, CONFIRMED)],
            source="dm",
        )  # fmt: skip
        names = [name("Claude Ambere", "person", "Claude"), name("Belleros", "person", "Belleros")]
        sale = {"a": "Belleros", "how": "owns", "b": item, "quote": SOLD, "from": "Claude",
                "change": "sold"}  # fmt: skip
        ai = lore_fake.lore_ai(json.dumps({"names": names, "connections": [sale]}))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(LATER, "a later note"))
        await it.response.sent[0][1]["view"]._read(self.it())
        dm = self.it(DM)
        return dm if await ui_bg.offer_ready(dm, self.campaign.id) else None

    async def test_the_sale_of_a_small_letter_item_is_offered(self) -> None:
        dm = await self.read("wolf figurine")
        assert dm is not None
        view = dm.followup.send.await_args.kwargs["view"]
        self.assertIn("**Belleros** owns **wolf figurine** (Claude Ambere sold it)", view.text())

    async def test_one_starting_with_the_is_still_left_out(self) -> None:
        # (the AI writes the end as the text does; "the wolf figurine" stays a description)
        self.assertIsNone(await self.read("the wolf figurine"))
