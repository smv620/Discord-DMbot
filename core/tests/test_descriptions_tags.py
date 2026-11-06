"""A description is not a name; an entry with no proper name gets a short tag (#3431).
Pure tests with invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import claims, descriptions, tags
from dmbot.memory.models import Entity


def _entity(name: str, *, description: str = "", unnamed: bool = False) -> Entity:
    return Entity(
        id="e" * 32,
        type="character",
        name=name,
        description=description,
        status="confirmed",
        merged_into=None,
        source="dm",
        created_at=0,
        unnamed=unnamed,
    )


class TheDescriptionRule(unittest.TestCase):
    def test_what_is_a_description(self) -> None:
        for text in ("the woman", "beggar woman", "a hooded figure", "his old sister"):
            self.assertTrue(descriptions.is_description(text), text)
        for text in (
            "Mira",
            "the Ashen King",
            "Beggar Mira",
            "Old Brannoc",
            "Ulric's Fall",
            "d'Artagnan",
            "al-Rashid",
            "Ulric’s Fall",
            "王",
        ):
            self.assertFalse(descriptions.is_description(text), text)

    def test_only_a_hand_typed_short_name_may_still_match(self) -> None:
        self.assertFalse(descriptions.matches_alone("the woman", "full", "background"))
        self.assertTrue(descriptions.matches_alone("the woman", "nickname", "dm"))
        self.assertTrue(descriptions.matches_alone("mira", "full", "dm"))
        self.assertFalse(descriptions.matches_alone("mira", "full", ""))
        self.assertTrue(descriptions.matches_alone("Mira", "full", "background"))


class TheMergedDescriptionNeverPlacesALine(unittest.TestCase):
    def test_the_woman_merged_into_beggar_woman_places_nothing(self) -> None:
        entities = [{"id": "a" * 32, "name": "Mira Vane", "type": "character"}]
        aliases = [
            {"entity_id": "a" * 32, "key": "mira vane", "text": "Mira Vane", "kind": "full"},
            {
                "entity_id": "a" * 32,
                "key": "the woman",
                "text": "the woman",
                "kind": "full",
                "source": "background",
            },
        ]
        names = claims.names_of(entities, aliases)
        self.assertEqual(names.one("the woman"), claims.UNKNOWN_NAME_REASON)
        self.assertEqual(names.one("Mira Vane"), ("a" * 32, "character"))

    def test_a_dm_typed_nickname_still_matches(self) -> None:
        entities = [{"id": "a" * 32, "name": "Mira Vane", "type": "character"}]
        aliases = [
            {
                "entity_id": "a" * 32,
                "key": "the ferrywoman",
                "text": "the ferrywoman",
                "kind": "nickname",
                "source": "dm",
            }
        ]
        self.assertEqual(
            claims.names_of(entities, aliases).one("the ferrywoman"), ("a" * 32, "character")
        )

    def test_an_alias_without_text_is_not_taken_for_a_description(self) -> None:
        entities = [{"id": "a" * 32, "name": "Mira Vane", "type": "character"}]
        aliases = [{"entity_id": "a" * 32, "key": "mira"}]
        self.assertEqual(claims.names_of(entities, aliases).one("mira"), ("a" * 32, "character"))


class TheTag(unittest.TestCase):
    place = tags.TagTie("located_in", "Termalaine", confirmed=True)
    group = tags.TagTie("member_of", "the Ash Wolves", confirmed=True)

    def test_a_proper_name_needs_no_tag(self) -> None:
        self.assertEqual(tags.tag_for(_entity("Mira Vane"), [self.place]), "Mira Vane")

    def test_the_place_comes_before_the_note(self) -> None:
        e = _entity("beggar woman", description="Warned the party about a doomed ship. More.")
        self.assertEqual(
            tags.tag_for(e, [self.group, self.place], dm=True), "beggar woman (in Termalaine)"
        )

    def test_a_group_tie_when_there_is_no_place(self) -> None:
        self.assertEqual(
            tags.tag_for(_entity("the woman"), [self.group]), "the woman (member of the Ash Wolves)"
        )

    def test_a_secret_tie_never_shows_to_a_player(self) -> None:
        secret = tags.TagTie("located_in", "the Hidden Vault", confirmed=True, secret=True)
        e = _entity("beggar woman")
        self.assertEqual(tags.tag_for(e, [secret]), "beggar woman")
        self.assertEqual(tags.tag_for(e, [secret], dm=True), "beggar woman (in the Hidden Vault)")

    def test_a_tie_to_an_entry_the_viewer_may_not_see_is_never_used(self) -> None:
        hidden = tags.TagTie("located_in", "the Hidden Vault", confirmed=True, visible=False)
        self.assertEqual(tags.tag_for(_entity("beggar woman"), [hidden], dm=True), "beggar woman")

    def test_a_long_place_is_cut_with_its_words_kept(self) -> None:
        far = tags.TagTie("located_in", "Z" * 70, confirmed=True)
        tag = tags.tag_for(_entity("beggar woman"), [far])
        self.assertTrue(tag.startswith("beggar woman (in Z"))
        self.assertLessEqual(len(tag), len("beggar woman ()") + tags.TAG_MAX)

    def test_family_ties_are_named_without_guessing_the_direction(self) -> None:
        kin_tie = tags.TagTie("related_to", "Mira Vane", confirmed=True, detail="sister")
        self.assertEqual(
            tags.tag_for(_entity("the woman"), [kin_tie]), "the woman (family of Mira Vane)"
        )

    def test_a_tie_not_confirmed_is_not_used(self) -> None:
        loose = tags.TagTie("located_in", "Termalaine", confirmed=False)
        self.assertEqual(tags.tag_for(_entity("beggar woman"), [loose]), "beggar woman")

    def test_the_note_is_the_dms_and_its_first_clause_is_cut(self) -> None:
        e = _entity("the woman", description="Gave a cryptic warning about a doomed ship, twice.")
        self.assertEqual(
            tags.tag_for(e, dm=True), "the woman (Gave a cryptic warning about a doomed ship)"
        )
        self.assertEqual(tags.tag_for(e), "the woman")
        long = _entity("the woman", description="x" * 20 + " " + "y" * 60)
        self.assertLessEqual(len(tags.tag_for(long, dm=True)), len("the woman ()") + tags.TAG_MAX)

    def test_an_unnamed_person_keeps_its_label(self) -> None:
        e = _entity("Ken's sister (name unknown)", unnamed=True)
        self.assertEqual(
            tags.tag_for(e, [self.place], shown_name="Ken's sister (name unknown)"),
            "Ken's sister (name unknown) (in Termalaine)",
        )


if __name__ == "__main__":
    unittest.main()
