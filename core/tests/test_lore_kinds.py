"""The connection kinds a lore text uses (#3033, #3035): rival, mentor, leads, founded, captive and
contains are kinds of their own, a family line keeps its role, a group can know, and a connection
whose two ends do not fit its kind is mapped (a place in a place) or dropped and counted. Invented
names and text."""

from __future__ import annotations

import json
import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.memory import lore_read
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED, NewName
from dmbot.ui import name_background as ui_bg
from tests.test_memory_names import GUILD, NamesTest

TEXT = (
    "Dorn and Ilse are the rival apprentices of Doran. Doran teaches Mira. Speaker Holt leads "
    "Brindle. Arven founded the Silver Lanterns. Tor is held captive by the circus master. "
    "The tome contains a note. Ilse is the sister of Dorn and Mira's husband is Tavin."
)


def read(word: str, a: str, b: str, quote: str) -> list[bg.Connection]:
    raw = json.dumps({"connections": [{"a": a, "how": word, "b": b, "quote": quote}]})
    return bg.read_connections(raw, TEXT)


class TheWords(unittest.TestCase):
    def test_each_new_word_is_a_kind_of_its_own(self) -> None:
        rivals = "Dorn and Ilse are the rival apprentices of Doran"
        captor = "Tor is held captive by the circus master"
        cases = [
            ("rival", "Dorn", "Doran", rivals, "rival_of"),
            ("mentor", "Doran", "Mira", "Doran teaches Mira", "mentor_of"),
            ("leads", "Speaker Holt", "Brindle", "Speaker Holt leads Brindle", "leads"),
            ("founded", "Arven", "Silver Lanterns", "Arven founded the Silver Lanterns", "founded"),
            ("captive", "Tor", "circus master", captor, "captive_of"),
            ("contains", "tome", "note", "The tome contains a note", "contains"),
        ]  # fmt: skip
        for word, a, b, quote, kind in cases:
            with self.subTest(word=word):
                (got,) = read(word, a, b, quote)
                self.assertEqual((got.how, got.words), (kind, ""))  # no fixed words any more

    def test_rival_apprentices_are_two_rival_connections_not_the_fallback(self) -> None:
        quote = "Dorn and Ilse are the rival apprentices of Doran"
        raw = json.dumps(
            {
                "connections": [
                    {"a": "Dorn", "how": "rival", "b": "Doran", "quote": quote},
                    {"a": "Ilse", "how": "rival", "b": "Doran", "quote": quote},
                ]
            }
        )  # fmt: skip
        got = bg.read_connections(raw, TEXT)
        self.assertEqual([(c.a, c.how) for c in got], [("Dorn", "rival_of"), ("Ilse", "rival_of")])
        self.assertNotIn("associated_with", {c.how for c in got})

    def test_a_family_line_keeps_its_role(self) -> None:
        sib = read("sibling", "Ilse", "Dorn", "Ilse is the sister of Dorn")
        spouse = read("spouse", "Tavin", "Mira", "Mira's husband is Tavin")
        parent = read("parent", "Doran", "Mira", "Doran teaches Mira")
        self.assertEqual([(c.how, c.words) for c in sib], [("kin_of", "sibling")])
        self.assertEqual([(c.how, c.words) for c in spouse], [("kin_of", "spouse")])
        self.assertEqual([(c.how, c.words) for c in parent], [("kin_of", "parent")])

    def test_a_gendered_role_word_is_kept_as_its_neutral_one(self) -> None:
        sister = read("sister", "Ilse", "Dorn", "Ilse is the sister of Dorn")
        self.assertEqual([(c.how, c.words) for c in sister], [("kin_of", "sibling")])
        # "a is the son of b" is "b is a parent of a"
        son = read("son", "Dorn", "Doran", "Dorn and Ilse are the rival apprentices of Doran")
        self.assertEqual(
            [(c.a, c.how, c.words, c.b) for c in son], [("Doran", "kin_of", "parent", "Dorn")]
        )

    def test_the_prompts_name_the_new_kinds_and_the_family_roles(self) -> None:
        asked = lore_read.shape_instructions()
        for word in ("captive", "contains", "sibling", "spouse", "rival", "leads", "founded"):
            self.assertIn(word, asked)
        for word in bg.ALL_WORDS:
            self.assertIn(word, asked)  # the schema's enum and the explanation agree
        read_text = lore_read.read_instructions()
        for phrase in ("is held captive by", "contains", "sibling of", "spouse of"):
            self.assertIn(phrase, read_text)


class TheEnds(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store

    async def names(self, *named: tuple[str, str, str | None]) -> dict[str, str]:
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName(n, t, CONFIRMED, role=r) for n, t, r in named], source="dm",
        )  # fmt: skip
        return {n: str(i) for (n, _, _), i in zip(named, made.value, strict=True)}

    async def queue(self, *links: bg.Connection) -> Counter[str]:
        found = [bg.Found(c.a, "npc", (), (), "") for c in links]
        lost: Counter[str] = Counter()
        await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, found, [list(links)], set(), dropped=lost
        )
        return lost

    async def waiting(self) -> list[tuple[str, str, str]]:
        return [
            (w.subject, w.predicate, w.object)
            for w in await self.store.waiting(GUILD, self.campaign.id)
        ]

    async def test_towns_of_a_region_are_in_it_and_never_members_of_it(self) -> None:
        await self.names(
            ("Ten Vales", "place", None), ("Arn", "place", None), ("Brey", "place", None)
        )
        lost = await self.queue(
            bg.Connection("Arn", "member_of", "Ten Vales", "Arn is a town of the Ten Vales"),
            bg.Connection("Brey", "member_of", "Ten Vales", "Brey is a town of the Ten Vales"),
        )
        got = await self.waiting()
        self.assertEqual({p for _, p, _ in got}, {"located_in"})
        self.assertEqual(len(got), 2)
        self.assertEqual(lost["bad_kind"], 0)

    async def test_a_connection_whose_ends_do_not_fit_is_dropped_and_counted(self) -> None:
        await self.names(("Mira", "npc", "npc"), ("Brindle", "place", None))
        lost = await self.queue(
            bg.Connection("Mira", "member_of", "Brindle", "Mira is a member of Brindle"),
            bg.Connection("Brindle", "leads", "Mira", "Brindle leads Mira"),
        )
        self.assertEqual(await self.waiting(), [])
        self.assertEqual(lost["bad_kind"], 2)

    async def test_a_character_leads_a_place_and_a_group_knows_a_person(self) -> None:
        await self.names(
            ("Speaker Holt", "npc", "npc"), ("Brindle", "place", None),
            ("Brotherhood", "faction", None), ("Danneth", "npc", "npc"),
        )  # fmt: skip
        lost = await self.queue(
            bg.Connection("Speaker Holt", "leads", "Brindle", "Speaker Holt leads Brindle"),
            bg.Connection(
                "Brotherhood", "knows", "Danneth", "The Brotherhood knows who Danneth is"
            ),
        )
        got = {(a, p, b) for a, p, b in await self.waiting()}
        self.assertEqual(
            got,
            {
                ("Speaker Holt", "leads", "Brindle"),
                ("Brotherhood", "knows", "Danneth"),
            },
        )
        self.assertEqual(lost["bad_kind"], 0)

    async def test_an_own_kind_and_the_catch_all_take_any_ends(self) -> None:
        await self.names(("Mira", "npc", "npc"), ("Brindle", "place", None))
        lost = await self.queue(
            bg.Connection("Mira", "associated_with", "Brindle", "Mira and Brindle", words="pays")
        )
        self.assertEqual(len(await self.waiting()), 1)
        self.assertEqual(lost["bad_kind"], 0)
