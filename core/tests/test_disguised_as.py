"""One character posing as another (#3091, part 4): the `disguised_as` kind. Two different
characters, secret by nature, offered no "same person" join, read both ways, named by its
synonyms, and shown to a player only when their characters know it. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import same_person
from dmbot.memory.link_words import CONNECTION_BACK, CONNECTION_WORDS, link_words
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.ontology import CORE_PREDICATES, DISGUISED_AS, SYNONYMS
from dmbot.ui import name_card
from tests.test_memory_store import GUILD_A
from tests.test_moved_known import MergeMovesConnections


class TheKind(unittest.TestCase):
    def test_it_reads_is_disguised_as_and_is_impersonated_by(self) -> None:
        self.assertEqual(CONNECTION_WORDS[DISGUISED_AS], "is disguised as")
        self.assertEqual(CONNECTION_BACK[DISGUISED_AS], "is impersonated by")
        self.assertEqual(link_words(DISGUISED_AS, ""), "is disguised as")

    def test_it_is_character_to_character_and_not_symmetric(self) -> None:
        (term,) = [t for t in CORE_PREDICATES if t.key == DISGUISED_AS]
        self.assertEqual((term.subject_types, term.object_types), (("character",), ("character",)))
        self.assertFalse(term.symmetric)
        self.assertIsNone(term.max_per_subject)  # (a doppelganger may wear many faces)
        self.assertEqual(term.inverse_label, "is impersonated by")

    def test_every_phrase_for_a_disguise_means_it(self) -> None:
        for phrase in (
            "posing as", "impersonating", "masquerading as", "pretending to be",
            "a doppelganger of", "wearing the face of",
        ):  # fmt: skip
            word = phrase.removeprefix("a ").replace(" ", "_")
            with self.subTest(phrase):
                self.assertEqual(SYNONYMS[word], DISGUISED_AS)

    def test_the_connect_menu_offers_it_forward_only_and_still_fits_and_founded_is_still_there(
        self,
    ) -> None:
        choices = name_card.connect_choices("Mira")
        values = [v for v, _, _ in choices]
        self.assertLessEqual(len(values), 25)
        self.assertIn(DISGUISED_AS, values)
        self.assertNotIn(DISGUISED_AS + name_card.BACK, values)
        self.assertIn("founded", values)  # the kind is still chosen, only its back reading is not
        self.assertNotIn("founded" + name_card.BACK, values)
        label = dict((v, lab) for v, lab, _ in choices)[DISGUISED_AS]
        self.assertEqual(label, "Mira is disguised as…")
        self.assertIn(DISGUISED_AS, name_card.NO_BACK_OPTION)
        self.assertIn("founded", name_card.NO_BACK_OPTION)


class TheSynonyms(unittest.TestCase):
    def test_each_synonym_belongs_to_the_disguise(self) -> None:
        (term,) = [t for t in CORE_PREDICATES if t.key == DISGUISED_AS]
        for word in term.synonyms:
            with self.subTest(word):
                self.assertEqual(SYNONYMS[word], DISGUISED_AS)
        for word in ("posing_as", "impersonating", "masquerading_as", "pretending_to_be",
                     "doppelganger_of", "wearing_the_face_of"):  # fmt: skip
            self.assertEqual(SYNONYMS[word], DISGUISED_AS)


def entity(eid: str, name: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": eid, "type": "character", "name": name, "description": kw.pop("description", ""),
        "status": "confirmed", "unnamed": False, "unnamed_what": None, "unnamed_role": None,
        **kw,
    }  # fmt: skip


class NoSamePersonOffer(unittest.TestCase):
    def rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        stranger = entity(
            "a" * 32, "the hooded stranger", unnamed=True, unnamed_what="a tall hooded woman",
            unnamed_role="guard",
        )  # fmt: skip
        valyra = entity("b" * 32, "Valyra", description="a tall hooded woman, the guard")
        return [stranger, valyra], []

    def test_without_a_disguise_they_may_be_offered(self) -> None:
        entries, relations = self.rows()
        self.assertTrue(same_person.from_rows(entries, [], relations, []))

    def test_a_disguise_between_them_in_either_direction_means_no_offer(self) -> None:
        entries, _ = self.rows()
        for subject, obj in (("a" * 32, "b" * 32), ("b" * 32, "a" * 32)):
            for status in ("confirmed", "proposed"):
                with self.subTest(subject=subject, status=status):
                    relation = {
                        "subject_id": subject, "object_id": obj, "predicate": DISGUISED_AS,
                        "status": status,
                    }  # fmt: skip
                    self.assertEqual(same_person.from_rows(entries, [], [relation], []), [])

    def test_a_rejected_disguise_does_not_stop_the_offer(self) -> None:
        entries, _ = self.rows()
        relation = {
            "subject_id": "a" * 32, "object_id": "b" * 32, "predicate": DISGUISED_AS,
            "status": "rejected",
        }  # fmt: skip
        self.assertTrue(same_person.from_rows(entries, [], [relation], []))


class TheStore(MergeMovesConnections):
    async def mine(self) -> list[str]:
        known = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        return [c.relation_id for c in known.connections]

    async def setUpPeople(self) -> tuple[str, str]:
        jax = await self.add("Jax", type="character", role="npc", status=CONFIRMED)
        valyra = await self.add("Valyra", type="character", role="npc", status=CONFIRMED)
        return jax, valyra

    async def disguise(self, jax: str, valyra: str, **kw: Any) -> Any:
        return await self.memory.add_relation(
            GUILD_A, self.c, jax, DISGUISED_AS, valyra, source="dm", confidence=1.0,
            status=CONFIRMED, **kw,
        )  # fmt: skip

    async def test_two_characters_are_accepted_and_a_new_disguise_is_secret(self) -> None:
        jax, valyra = await self.setUpPeople()
        made = await self.disguise(jax, valyra, secret=False)  # (even when asked otherwise)
        relation, flags = made.value
        self.assertEqual(flags, [])
        self.assertTrue(relation.secret)
        self.assertEqual((relation.subject_id, relation.object_id), (jax, valyra))

    async def test_a_guess_or_an_upload_is_secret_too_and_stays_a_proposal(self) -> None:
        jax, valyra = await self.setUpPeople()
        made = await self.memory.add_relation(
            GUILD_A, self.c, jax, DISGUISED_AS, valyra, source="extractor", confidence=0.4,
        )  # fmt: skip
        relation = made.value[0]
        self.assertTrue(relation.secret)
        self.assertEqual(relation.status, "proposed")

    async def test_the_dm_reveals_it_with_the_connections_own_switch(self) -> None:
        jax, valyra = await self.setUpPeople()
        relation = (await self.disguise(jax, valyra)).value[0]
        done = await self.memory.update_relation(
            GUILD_A, self.c, relation.id, source="dm", secret=False
        )
        self.assertFalse(done.value.secret)
        again = (await self.disguise(jax, valyra)).value[0]  # stated again: the choice stays
        self.assertFalse(again.secret)

    async def test_a_character_cannot_be_disguised_as_itself(self) -> None:
        jax, _ = await self.setUpPeople()
        with self.assertRaises(MemoryRuleError):
            await self.disguise(jax, jax)

    async def test_a_place_at_either_end_is_a_problem_for_the_dm(self) -> None:
        jax, _ = await self.setUpPeople()
        keep = await self.add("The Keep", type="place", status=CONFIRMED)
        _, flags = (await self.disguise(jax, keep)).value
        self.assertTrue(flags)

    async def test_one_character_may_wear_several_faces_and_be_copied_by_several(self) -> None:
        jax, valyra = await self.setUpPeople()
        mara = await self.add("Mara", type="character", role="npc", status=CONFIRMED)
        await self.disguise(jax, valyra)
        _, flags = (await self.disguise(jax, mara)).value  # a second face
        self.assertEqual(flags, [])
        _, flags = (await self.disguise(mara, valyra)).value  # a second copy of Valyra
        self.assertEqual(flags, [])

    async def test_a_player_whose_characters_do_not_know_sees_nothing_and_one_who_knows_sees_it(
        self,
    ) -> None:
        jax, valyra = await self.setUpPeople()
        relation = (await self.disguise(jax, valyra)).value[0]
        self.assertNotIn(relation.id, await self.mine())
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, relation.id, [self.pc], source="dm"
        )
        self.assertIn(relation.id, await self.mine())
