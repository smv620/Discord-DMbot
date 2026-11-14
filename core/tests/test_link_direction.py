"""A connection's direction, the DM's choice (#4154): A → B, B → A or A ↔ B, set on the store,
the website's door and Discord's edit page alike, each with Undo; and the "A and B are rivals"
words both take from one table. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.entity_text import _connection
from dmbot.memory.gate import CONNECTION
from dmbot.memory.link_words import (
    PAIR_WORDS,
    back_words,
    pair_words,
    reads_both_ways,
)
from dmbot.memory.models import MemoryRuleError, Relation
from dmbot.memory.ontology import PredicateTerm
from dmbot.memory.store import DIRECTION_DM_ONLY, ROLE_ONE_WAY
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
            "next_to": "are next to each other",  # (#4173 slice 6a; "borders": "share a border")
            "route_to": "are connected",  # (#4173 slice 6d; "road": "are connected by road")
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
        self.assertEqual(pair_words("knows", "", "aware"), "know of each other")
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

    def test_a_player_never_reads_a_feeling_from_the_other_end(self) -> None:
        """#3611: the DM's old friend and enemy lines are plain "knows" to anyone else."""
        for predicate in ("ally_of", "enemy_of"):
            row = {
                "id": "r1", "a": "bb", "b": "aa", "predicate": predicate, "detail": "",
                "secret": False, "at": 1, "both_ways": False,
            }  # fmt: skip
            with self.subTest(predicate):
                (edge,) = relationships.edges_from([row], can_edit=False, is_dm=False)
                back = str(edge["links"][0]["back"])
                self.assertNotIn("friend", back)
                self.assertNotIn("enemy", back)


class _StoreBase(MemoryTest):  # (no tests of its own: both classes below share it)
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

    async def live(self, predicate: str) -> list[Relation]:
        return [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.predicate == predicate and r.status == "confirmed"
        ]

    async def turn(self, rid: str, first: str | None) -> Any:
        return await self.memory.set_direction(GUILD_A, self.c, rid, first, source="dm")

    async def refused(self, rid: str, first: str | None, **kw: Any) -> str:
        with self.assertRaises(MemoryRuleError) as caught:
            await self.memory.set_direction(GUILD_A, self.c, rid, first, **{"source": "dm", **kw})
        return str(caught.exception)


class TheStore(_StoreBase):
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

    async def test_a_turn_writes_it_anew_keeping_secret_pin_and_knowers_and_undo_restores(
        self,
    ) -> None:
        hero = await self.add(
            "Hero", type="character", role="player_character", played_by=8, status="confirmed"
        )
        r = await self.tie("sworn_to", self.cerric, self.wit, secret=True)
        await self.memory.pin(GUILD_A, self.c, r.id, True, source="dm")
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, r.id, [hero], source="dm"
        )
        done = await self.turn(r.id, self.wit)
        new = done.value
        self.assertNotEqual(new.id, r.id)
        self.assertEqual((new.subject_id, new.object_id, new.secret), (self.wit, self.cerric, True))
        self.assertEqual((await self.stored(r.id)).status, "rejected")  # (a correction, no history)
        self.assertEqual(await self.memory.pinned_connections(GUILD_A, self.c), {new.id})
        known = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [new.id])
        self.assertEqual(known.get(new.id), [hero])
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertEqual([x.id for x in await self.live("sworn_to")], [r.id])

    async def test_a_turned_rivalry_said_again_stays_one_and_turned(self) -> None:
        r = await self.tie("rival_of", self.cerric, self.wit)
        _low, high = sorted((self.cerric, self.wit))
        turned = (await self.turn(r.id, high)).value
        self.assertEqual((turned.subject_id, turned.both_ways), (high, False))
        await self.relate(self.cerric, "rival_of", self.wit, status="confirmed")
        (only,) = await self.live("rival_of")
        self.assertEqual((only.id, only.subject_id), (turned.id, high))

    async def test_a_merge_keeps_the_dms_direction(self) -> None:
        r = await self.tie("rival_of", self.cerric, self.wit)
        _low, high = sorted((self.cerric, self.wit))
        await self.turn(r.id, high)
        twin = await self.add("Cerric the Bold", type="character")
        await self.memory.merge(GUILD_A, self.c, self.cerric, twin, source="dm")
        (only,) = await self.live("rival_of")
        self.assertEqual((only.subject_id, only.both_ways), (high, False))

    async def test_a_family_role_turns_with_its_names_and_reads_one_way(self) -> None:
        niece = await self.tie("kin_of", self.cerric, self.wit, detail="niece", directional=True)
        turned = (await self.turn(niece.id, self.wit)).value
        self.assertEqual(
            (turned.subject_id, turned.detail, turned.directional), (self.wit, "niece", True)
        )  # ("Wit is the niece of Cerric": the correction when it was written the wrong way)
        self.assertEqual(await self.refused(turned.id, None), ROLE_ONE_WAY)

    async def test_an_old_undirected_role_takes_the_arrow(self) -> None:
        sister = await self.tie("kin_of", self.cerric, self.wit, detail="sister")
        self.assertFalse(sister.directional)
        done = await self.turn(sister.id, sister.subject_id)
        self.assertEqual((done.value.directional, done.value.both_ways), (True, None))

    async def test_a_turn_the_rules_refuse_writes_nothing(self) -> None:
        """A turn is a new connection's write, with its checks (the reviewer's B2): a player
        character never travels with its own companion."""
        hero = await self.add(
            "Hero", type="character", role="player_character", played_by=8, status="confirmed"
        )
        r = await self.tie("travels_with", self.cerric, hero)
        with self.assertRaises(MemoryRuleError):
            await self.turn(r.id, hero)
        left = [(x.id, x.subject_id, x.both_ways) for x in await self.live("travels_with")]
        self.assertEqual(left, [(r.id, self.cerric, None)])

    async def test_a_words_change_keeps_the_dms_direction(self) -> None:
        r = await self.tie("knows", self.cerric, self.wit)
        both = (await self.turn(r.id, None)).value
        changed = await self.memory.change_relation_words(
            GUILD_A, self.c, both.id, "knows", "wary", source="dm", dm_feeling=True
        )
        self.assertIs(changed.value.both_ways, True)

    async def test_a_twin_the_other_way_round_takes_over(self) -> None:
        r = await self.tie("sworn_to", self.cerric, self.wit)
        twin = await self.tie("sworn_to", self.wit, self.cerric)
        done = await self.turn(r.id, self.wit)
        self.assertEqual(done.value.id, twin.id)
        self.assertEqual([x.id for x in await self.live("sworn_to")], [twin.id])

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
                polled = await self.poll(answer)
                self.assertEqual(polled["state"], "done")
                (link,) = polled["edge"]["links"]  # (a turn writes it anew: the id may change)
                rid = link["id"]
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

    async def test_both_ways_on_a_one_way_family_role_fails(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "kin_of", self.rook, source="dm", confidence=1.0,
            status="confirmed", detail="niece", directional=True,
        )  # fmt: skip
        answer = await self.edit(made.value[0].id, action="direction", direction="both")
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

    def test_three_choices_each_saying_its_sentence(self) -> None:
        from dmbot.ui.connection_edit import BOTH, BOTH_LABEL, direction_choices

        def said(r: Relation) -> str:
            return f"{r.subject_id} {r.detail or r.predicate} {r.object_id} {r.both_ways}"

        one_way = direction_choices(self.rel("sworn_to"), "Cerric", "Wit", said)
        self.assertEqual(
            [(o.label, o.value, o.default, o.description) for o in one_way],
            [("Cerric → Wit", "c1", True, "c1 sworn_to w1 False"),
             ("Wit → Cerric", "w1", False, "w1 sworn_to c1 False"),
             (BOTH_LABEL, BOTH, False, "c1 sworn_to w1 True")],
        )  # fmt: skip
        both = direction_choices(self.rel("rival_of"), "Cerric", "Wit", said)
        self.assertEqual([o.default for o in both], [False, False, True])
        niece = Relation(
            "r1", "c1", "kin_of", "w1", "niece", 1.0, "confirmed", "dm", (), None, None, None,
            None, False, 0, directional=True,
        )  # fmt: skip
        turned = direction_choices(niece, "Mira", "Aldric", said)
        self.assertEqual([o.value for o in turned], ["c1", "w1"])  # (no both ways for a niece)
        self.assertEqual(turned[1].description, "w1 niece c1 None")  # (the role keeps its word)


class TheDirectionsUndo(Editing):
    """#4156's Undo takes back a direction change, a turn included (written anew, one batch)."""

    async def test_a_turn_from_the_website_is_undone_whole(self) -> None:
        rid = self.links["mira-rook"]
        answer = await self.edit(rid, action="direction", direction="b-a")
        await self.apply_all()
        polled = await self.poll(answer)
        self.assertIsNotNone(polled["batch"])
        undo = await relationships.queue_link_undo(
            self.web, self.session, self.cref, str(polled["batch"])
        )
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertTrue((await self.poll(undo))["undone"])
        stored = await self.memory.relation(self.g, self.one.id, rid)
        assert stored is not None
        self.assertEqual((stored.status, stored.both_ways), ("confirmed", None))


class ACampaignsOwnKind(_StoreBase):
    """A campaign's own kind (#4354): its default lives in its table (`is_symmetric`), which the
    card's words never read, so the row carries the DM's choice and every reader agrees."""

    async def own(self, symmetric: bool) -> str:
        key = "oath_kin_of" if symmetric else "patron_of"
        term = PredicateTerm(
            key, "is oath-bound to", "Two sworn to each other.", ("character",), ("character",),
            symmetric=symmetric, core=False,
        )  # fmt: skip
        await self.memory.add_predicate(
            GUILD_A, self.c, term, examples=["Cerric is oath-bound to Wit."],
            reason="the table uses it", source="dm",
        )  # fmt: skip
        return key

    def text_of_other_end(self, r: Relation, symmetric: bool) -> str | None:
        return _connection(
            {"predicate": r.predicate, "label": "is oath-bound to", "other": "Cerric",
             "outgoing": False, "symmetric": symmetric, "both_ways": r.both_ways}
        )  # fmt: skip

    async def test_a_two_way_own_kind_made_one_way_reads_one_way_everywhere(self) -> None:
        key = await self.own(symmetric=True)
        r = await self.tie(key, self.cerric, self.wit)
        one = (await self.turn(r.id, r.subject_id)).value
        self.assertIs(one.both_ways, False)
        self.assertFalse(reads_both_ways(one.predicate, one.detail, False, one.both_ways))  # card
        self.assertIsNone(self.text_of_other_end(one, symmetric=True))  # search text
        back = (await self.turn(r.id, None)).value
        self.assertIs(back.both_ways, True)
        self.assertTrue(reads_both_ways(back.predicate, back.detail, False, back.both_ways))
        self.assertIsNotNone(self.text_of_other_end(back, symmetric=True))

    async def test_a_one_way_own_kind_made_two_way_and_back(self) -> None:
        key = await self.own(symmetric=False)
        r = await self.tie(key, self.cerric, self.wit)
        same = await self.turn(r.id, r.subject_id)  # (the way it already reads: nothing written)
        self.assertIsNone(same.batch)
        both = (await self.turn(r.id, None)).value
        self.assertIs(both.both_ways, True)
        one = (await self.turn(r.id, r.subject_id)).value
        self.assertIsNone(one.both_ways)  # (its kind's own default again: one way everywhere)
        self.assertFalse(reads_both_ways(one.predicate, one.detail, False, one.both_ways))
        self.assertIsNone(self.text_of_other_end(one, symmetric=False))

    async def test_a_two_way_own_kind_turned_round(self) -> None:
        key = await self.own(symmetric=True)
        r = await self.tie(key, self.cerric, self.wit)
        done = await self.turn(r.id, r.object_id)
        new = done.value
        self.assertEqual(
            (new.subject_id, new.object_id, new.both_ways), (r.object_id, r.subject_id, False)
        )
        self.assertEqual((await self.stored(r.id)).status, "rejected")
        self.assertEqual([x.id for x in await self.live(key)], [new.id])
        await self.memory.undo(GUILD_A, self.c, done.batch)
        (back,) = await self.live(key)
        self.assertEqual((back.id, back.both_ways), (r.id, True))  # (its own mark, #4388)

    async def test_a_new_two_way_own_kind_reads_two_way_everywhere(self) -> None:
        # (#4388: the row says so, so the card and the search text agree from the start)
        r = await self.tie(await self.own(symmetric=True), self.cerric, self.wit)
        self.assertIs(r.both_ways, True)
        self.assertTrue(reads_both_ways(r.predicate, r.detail, False, r.both_ways))
        self.assertIsNotNone(self.text_of_other_end(r, symmetric=True))

    async def test_a_turned_own_kind_said_again_stays_one_and_turned(self) -> None:
        # (#4388, as `test_a_turned_rivalry_said_again_stays_one_and_turned` for a core kind)
        key = await self.own(symmetric=True)
        r = await self.tie(key, self.cerric, self.wit)
        _low, high = sorted((self.cerric, self.wit))
        turned = (await self.turn(r.id, high)).value
        self.assertEqual((turned.subject_id, turned.both_ways), (high, False))
        await self.relate(self.cerric, key, self.wit, status="confirmed")
        (only,) = await self.live(key)
        self.assertEqual((only.id, only.subject_id), (turned.id, high))
