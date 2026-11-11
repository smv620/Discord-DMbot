"""A connection's direction, the DM's choice (#4154): A → B, B → A or A ↔ B, set on the store,
the website's door and Discord's edit page alike, each with Undo; and the "A and B are rivals"
words both take from one table. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import kin
from dmbot.memory.link_words import (
    PAIR_WORDS,
    back_words,
    pair_words,
    reads_both_ways,
)
from dmbot.memory.models import MemoryRuleError, Relation
from dmbot.memory.store import (
    DIRECTION_DM_ONLY,
    ROLE_CANT_TURN,
    ROLE_ONE_WAY,
    TWIN_THE_OTHER_WAY,
)
from dmbot.web import relationships
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_web_link_actions import Editing


class TheWords(unittest.TestCase):
    def test_every_pair_word(self) -> None:
        expected = {
            "rival_of": "are rivals",
            "ally_of": "are friends or allies",
            "enemy_of": "are enemies",
            "kin_of": "are family",
            "knows": "know each other",
            "travels_with": "travel together",
            "associated_with": "are connected",
        }
        self.assertEqual(PAIR_WORDS, expected)
        for predicate, words in expected.items():
            with self.subTest(predicate):
                self.assertEqual(pair_words(predicate), words)

    def test_roles_meetings_and_the_fallbacks(self) -> None:
        self.assertEqual(pair_words("kin_of", "cousin"), "are cousins")
        self.assertEqual(pair_words("kin_of", "spouse"), "are married")
        self.assertEqual(pair_words("kin_of", "sibling"), "are siblings")
        self.assertEqual(pair_words("kin_of", "niece"), "are family")  # (an undirected old row)
        self.assertEqual(pair_words("knows", "", "met"), "have met")
        self.assertEqual(pair_words("knows", "", "aware"), "know each other")
        self.assertEqual(
            pair_words("associated_with", "sworn on the ashes of"),
            "are connected (“sworn on the ashes of”)",
        )
        self.assertEqual(pair_words("sworn_to"), "are connected (“is sworn to”)")

    def test_the_dms_mark_wins_except_over_a_one_way_role(self) -> None:
        self.assertTrue(reads_both_ways("rival_of"))
        self.assertFalse(reads_both_ways("rival_of", both_ways=False))
        self.assertTrue(reads_both_ways("sworn_to", both_ways=True))
        self.assertFalse(reads_both_ways("sworn_to"))
        self.assertFalse(reads_both_ways("kin_of", "niece", True, both_ways=True))

    def test_a_two_way_kind_made_one_way_reads_back(self) -> None:
        self.assertEqual(back_words("rival_of", "", both_ways=False), "is seen as a rival by")
        self.assertIsNone(back_words("rival_of", ""))
        self.assertEqual(back_words("kin_of", "", both_ways=False), "is family of")

    def test_a_role_turns_round_to_a_neutral_word(self) -> None:
        for role, turned in (
            ("niece", "aunt or uncle"), ("uncle", "niece or nephew"), ("mother", "child"),
            ("son", "parent"), ("grandmother", "grandchild"), ("sister", "sibling"),
            ("wife", "spouse"), ("cousin", "cousin"), ("guardian", None),
        ):  # fmt: skip
            with self.subTest(role):
                self.assertEqual(kin.opposite(role), turned)


class TheStore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cerric = await self.add("Cerric", type="character")
        self.wit = await self.add("Wit", type="character")

    async def tie(self, predicate: str, a: str, b: str, **kw: Any) -> Relation:
        made = await self.relate(a, predicate, b, status="confirmed", **kw)
        return made.value[0]  # type: ignore[no-any-return]

    async def stored(self, rid: str) -> Relation:
        got = await self.memory.relation(GUILD_A, self.c, rid)
        assert got is not None
        return got

    async def turn(self, rid: str, first: str | None) -> Any:
        return await self.memory.set_direction(GUILD_A, self.c, rid, first, source="dm")

    async def refused(self, rid: str, first: str | None, **kw: Any) -> str:
        with self.assertRaises(MemoryRuleError) as caught:
            await self.memory.set_direction(GUILD_A, self.c, rid, first, **{"source": "dm", **kw})
        return str(caught.exception)

    async def test_both_ways_on_a_one_way_kind_and_its_undo(self) -> None:
        r = await self.tie("sworn_to", self.cerric, self.wit)
        done = await self.turn(r.id, None)
        self.assertEqual((done.value.both_ways, done.value.id), (True, r.id))
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertIsNone((await self.stored(r.id)).both_ways)

    async def test_one_way_on_a_rivalry_then_back_to_its_default(self) -> None:
        r = await self.tie("rival_of", self.cerric, self.wit)
        one = await self.turn(r.id, r.subject_id)
        self.assertIs(one.value.both_ways, False)
        back = await self.turn(r.id, None)
        self.assertIsNone(back.value.both_ways)  # (the kind's default: nothing stored)

    async def test_a_flip_swaps_the_names_keeps_the_id_and_undo_swaps_back(self) -> None:
        r = await self.tie("sworn_to", self.cerric, self.wit, secret=True)
        await self.memory.pin(GUILD_A, self.c, r.id, True, source="dm")
        done = await self.turn(r.id, self.wit)
        flipped = await self.stored(r.id)
        self.assertEqual((flipped.subject_id, flipped.object_id), (self.wit, self.cerric))
        self.assertTrue(flipped.secret)  # (a secret stays secret through a flip)
        self.assertEqual(await self.memory.pinned_connections(GUILD_A, self.c), {r.id})
        await self.memory.undo(GUILD_A, self.c, done.batch)
        again = await self.stored(r.id)
        self.assertEqual((again.subject_id, again.object_id), (self.cerric, self.wit))

    async def test_a_family_role_turns_round_or_is_refused(self) -> None:
        niece = await self.tie("kin_of", self.cerric, self.wit, detail="niece", directional=True)
        await self.turn(niece.id, self.wit)
        turned = await self.stored(niece.id)
        self.assertEqual(
            (turned.subject_id, turned.detail, turned.directional),
            (self.wit, "aunt or uncle", True),
        )
        self.assertEqual(await self.refused(niece.id, None), ROLE_ONE_WAY)
        ward = await self.add("Pell", type="character")
        guardian = await self.tie("kin_of", self.cerric, ward, detail="guardian", directional=True)
        self.assertEqual(await self.refused(guardian.id, ward), ROLE_CANT_TURN)

    async def test_a_twin_the_other_way_round_is_refused(self) -> None:
        r = await self.tie("sworn_to", self.cerric, self.wit)
        await self.tie("sworn_to", self.wit, self.cerric)
        self.assertEqual(await self.refused(r.id, self.wit), TWIN_THE_OTHER_WAY)

    async def test_the_same_direction_writes_nothing_and_only_the_dm_sets_it(self) -> None:
        r = await self.tie("sworn_to", self.cerric, self.wit)
        self.assertIsNone((await self.turn(r.id, self.cerric)).batch)
        self.assertEqual(await self.refused(r.id, None, source="ai"), DIRECTION_DM_ONLY)


class TheWebsite(Editing):
    def link_of(self, page: dict[str, Any], rid: str) -> dict[str, Any]:
        return next(x for e in page["edges"] for x in e["links"] if x["id"] == rid)

    async def test_the_door_sets_each_direction_and_the_page_reads_it(self) -> None:
        rid = self.links["mira-rook"]  # (an ally: both ways by default)
        page = await self.graph(center=self.mira, hops=1)
        self.assertEqual(self.link_of(page, rid)["direction"], "both")
        self.assertEqual(self.link_of(page, rid)["pairWords"], "are friends or allies")
        low = min(self.mira, self.rook)
        for direction in ("b-a", "a-b", "both"):
            with self.subTest(direction):
                answer = await self.edit(rid, action="direction", direction=direction)
                self.assertEqual([o.error for o in await self.apply_all()], [""])
                self.assertEqual((await self.poll(answer))["state"], "done")
                link = self.link_of(await self.graph(center=self.mira, hops=1), rid)
                self.assertEqual(link["direction"], direction)
                self.assertEqual("pairWords" in link, direction == "both")
                stored = await self.memory.relation(self.g, self.one.id, rid)
                assert stored is not None
                if direction != "both":
                    self.assertEqual(stored.subject_id == low, direction == "a-b")

    async def test_a_player_reads_the_direction_and_never_gets_the_door(self) -> None:
        rid = await self.link("rival_of", self.mira, self.rook, "rivals")
        assert self.hero is not None
        await self.tell_the_table(self.hero)
        link = self.link_of(await self.graph(self.player, center=self.mira, hops=1), rid)
        self.assertEqual((link["direction"], link["pairWords"]), ("both", "are rivals"))
        # (an old friend line reads as plain "knows" to a player, one way, as before: #3611)
        old = self.link_of(
            await self.graph(self.player, center=self.mira, hops=1), self.links["mira-rook"]
        )
        self.assertNotIn("pairWords", old)
        self.assertEqual(
            await self.refused_edit(self.player, link=rid, action="direction", direction="both"),
            "not_allowed",
        )

    async def test_a_wrong_direction_is_refused(self) -> None:
        rid = self.links["mira-rook"]
        for bad in ("a_b", "", None, "both "):
            with self.subTest(bad):
                self.assertEqual(
                    await self.refused_edit(link=rid, action="direction", direction=bad),
                    "bad_action",
                )

    async def test_a_family_role_that_cant_turn_fails_plainly(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "kin_of", self.rook, source="dm", confidence=1.0,
            status="confirmed", detail="guardian", directional=True,
        )  # fmt: skip
        rid = made.value[0].id
        towards = "b-a" if min(self.mira, self.rook) == self.mira else "a-b"
        answer = await self.edit(rid, action="direction", direction=towards)
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["state"], "failed")


class TheEdgeWords(unittest.TestCase):
    def row(self, predicate: str, **kw: Any) -> dict[str, Any]:
        return {
            "id": "r1", "a": "aa", "b": "bb", "predicate": predicate, "detail": "",
            "secret": False, "at": 1, **kw,
        }  # fmt: skip

    def test_a_one_way_kind_made_two_way_and_a_rivalry_made_one_way(self) -> None:
        (edge,) = relationships.edges_from(
            [self.row("sworn_to", both_ways=True)], can_edit=True, is_dm=True
        )
        link = edge["links"][0]
        self.assertEqual(
            (link["direction"], link["pairWords"]), ("both", "are connected (“is sworn to”)")
        )
        (edge,) = relationships.edges_from(
            [self.row("rival_of", a="bb", b="aa", both_ways=False)], can_edit=True, is_dm=True
        )
        link = edge["links"][0]
        self.assertEqual((link["direction"], link["back"]), ("b-a", "is seen as a rival by"))
        self.assertNotIn("pairWords", link)


class TheDiscordMenu(unittest.TestCase):
    def rel(self, predicate: str, both_ways: bool | None = None) -> Relation:
        return Relation(
            "r1", "c1", predicate, "w1", "", 1.0, "confirmed", "dm", (), None, None, None, None,
            False, 0, both_ways=both_ways,
        )  # fmt: skip

    def test_three_choices_with_the_one_it_reads_now_chosen(self) -> None:
        from dmbot.ui.connection_edit import BOTH, direction_choices

        one_way = direction_choices(self.rel("sworn_to"), "Cerric", "Wit")
        self.assertEqual(
            [(o.label, o.value, o.default) for o in one_way],
            [("Cerric → Wit", "c1", True), ("Wit → Cerric", "w1", False),
             ("Cerric ↔ Wit", BOTH, False)],
        )  # fmt: skip
        both = direction_choices(self.rel("rival_of"), "Cerric", "Wit")
        self.assertEqual([o.default for o in both], [False, False, True])
