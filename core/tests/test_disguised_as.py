"""One character posing as another (#3091, part 4): the `disguised_as` kind. Two different
characters, secret by nature, offered no "same person" join, read both ways, named by its
synonyms, and shown to a player only when their characters know it. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import background as bg
from dmbot.memory import lore_read, same_person
from dmbot.memory.claims import place_relationship
from dmbot.memory.link_words import (
    CONNECTION_BACK,
    CONNECTION_WORDS,
    link_words,
    predicate_for,
)
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.ontology import CORE_PREDICATES, DISGUISED_AS, SYNONYMS, Ontology
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests import test_memory_names as tmn
from tests import test_memory_store as tms
from tests import test_moved_known as moved
from tests.test_memory_store import GUILD_A


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
        self.assertEqual(len(values), 25)
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


class TheStore(moved.MergeMovesConnections):
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


PHRASES = (
    "is posing as",
    "is impersonating",
    "is pretending to be",
    "is a doppelganger of",
    "is masquerading as",
    "is wearing the face of",
)


class TheUsualPhrasings(unittest.TestCase):
    def test_each_phrase_places_on_the_disguise_from_a_claim(self) -> None:
        onto = Ontology.build()
        for phrase in PHRASES:
            with self.subTest(phrase):
                term, from_object, label = place_relationship(
                    onto, phrase, "character", "character"
                )
                self.assertEqual((term.key, from_object, label), (DISGUISED_AS, False, ""))

    def test_each_phrase_typed_on_the_website_is_the_disguise_not_the_catch_all(self) -> None:
        for phrase in PHRASES:
            with self.subTest(phrase):
                self.assertEqual(predicate_for(phrase), (DISGUISED_AS, ""))

    def test_a_guard_disguised_as_a_guard_is_not_posing_as_a_particular_character(self) -> None:
        self.assertNotIn("disguised_as_a", SYNONYMS)


class TheLoreReads(unittest.TestCase):
    TEXT = "Jax, a doppelganger, poses as Valyra, the guard captain."

    def test_a_pose_as_is_read_as_a_connection(self) -> None:
        raw = (
            '{"connections": [{"a": "Jax", "how": "disguised as", "b": "Valyra",'
            ' "quote": "Jax, a doppelganger, poses as Valyra"}]}'
        )
        (got,) = bg.read_connections(raw, self.TEXT)
        self.assertEqual((got.a, got.how, got.b), ("Jax", DISGUISED_AS, "Valyra"))

    def test_the_prompts_draw_the_line_between_a_second_name_and_a_second_person(self) -> None:
        first = lore_read.read_instructions()
        second = lore_read.shape_instructions()
        self.assertIn("SECOND PERSON POSING AS ANOTHER", first)
        self.assertIn("is disguised as", first)
        self.assertIn("never put under 'secret:'", first)
        self.assertIn("ONE PERSON, TWO NAMES", first)  # a secret identity of one person stays
        self.assertIn("another name of the SAME person", first)
        self.assertIn("disguised as = a is disguised as b", second)


class ThePairNeverOffered(tms.ConfirmedMemoryTest):
    async def test_a_proposed_disguise_between_an_unnamed_and_a_named_person_means_no_offer(
        self,
    ) -> None:
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="guard at the north gate", role="guard"
        )
        guard = made.entity.id
        ken = await self.add(
            "Ken",
            type="character",
            status=CONFIRMED,
            description="A guard who keeps the north gate",
        )
        self.assertEqual(
            [
                (o.unnamed_id, o.named_id)
                for o in await self.memory.same_person_offers(GUILD_A, self.c)
            ],
            [(guard, ken)],
        )  # (offered while nothing joins them)
        await self.memory.add_relation(
            GUILD_A, self.c, guard, DISGUISED_AS, ken, source="extractor", confidence=0.4
        )  # a guess: proposed
        self.assertEqual(await self.memory.same_person_offers(GUILD_A, self.c), [])


class TheConnectMenuSaysItTruthfully(tmn.NamesTest):
    async def test_a_new_disguise_ends_with_the_line_about_showing_it(self) -> None:
        jax = await ui.save_name(self.memory, self.campaign, "Jax", "npc", [], [])
        valyra = await ui.save_name(self.memory, self.campaign, "Valyra", "npc", [], [])
        it = self.it()
        await name_card.connect(it, self.campaign.id, jax.id, DISGUISED_AS, valyra.id)
        shown = it.response.edited[0][0]
        self.assertIn("🧭 Saved: **Jax** is disguised as **Valyra**", shown)
        self.assertIn("players can't see it. " + name_card.DISGUISE_SECRET, shown)
        self.assertNotIn(name_card.SECRET_ALREADY, shown)
        self.assertEqual(
            name_card.DISGUISE_SECRET,
            "A disguise starts secret. Show it to the players with the 🤫 switch on its card.",
        )


class TheOneInDisguiseKnowsIt(TheStore):
    async def test_a_player_character_in_disguise_and_only_their_player_sees_it(self) -> None:
        _, valyra = await self.setUpPeople()
        relation = (await self.disguise(self.pc, valyra)).value[0]  # the player's own character
        self.assertIn(relation.id, await self.mine())
        stranger = await self.memory.what_my_characters_know(GUILD_A, self.c, 999)
        self.assertEqual([c.relation_id for c in stranger.connections], [])

    async def test_the_listing_is_made_on_every_path_not_only_the_dms(self) -> None:
        jax, valyra = await self.setUpPeople()
        made = await self.memory.add_relation(
            GUILD_A, self.c, jax, DISGUISED_AS, valyra, source="extractor", confidence=0.4
        )
        known = await self.memory.statement_knowers(
            GUILD_A, self.c, "connection", [made.value[0].id]
        )
        jax_view = next(
            p.id for p in await self.memory.perspectives(GUILD_A, self.c) if p.entity_id == jax
        )
        self.assertTrue(known.get(made.value[0].id))
        _ = jax_view
