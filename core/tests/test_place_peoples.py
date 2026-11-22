"""A place's culture and its people by set phrases (#4257, MapBot 3a; Supervisor's ruling): a
settlement word gives a culture, ruins give "once", a quarter gives a part; who lives there gives a
population with its share or none; never a number; never a character's own lineage. Invented
places and people. Pure."""

from __future__ import annotations

import unittest

from dmbot.memory import place_peoples as pp
from dmbot.memory.place_peoples import CULTURE, POPULATION, Found


class Culture(unittest.TestCase):
    def test_a_settlement_word_gives_a_strong_culture(self) -> None:
        for text in (
            "They rested in the dwarven hold of Karnstead before the pass.",
            "Karnstead, a dwarven hold, guards the pass.",
        ):
            with self.subTest(text):
                self.assertEqual(pp.read("Karnstead", text), [Found(CULTURE, "dwarf", "strong")])

    def test_is_a_gives_the_same_shapes_and_was_a_gives_once(self) -> None:  # (ruling A, #4982)
        self.assertEqual(pp.read("Karnstead", "Karnstead is a dwarven hold."),
                         [Found(CULTURE, "dwarf", "strong")])  # fmt: skip
        self.assertEqual(pp.read("Karnstead", "Karnstead was an elven town."),
                         [Found(CULTURE, "elf", "once")])  # fmt: skip
        for text in (
            "Karnstead is a dwarven smith's home.",  # (a person's lineage, never the place's)
            "Karnstead is the dwarven quarter.",  # (a part, said this way, is not read)
            "Karnstead is a busy town.",
        ):
            with self.subTest(text):
                self.assertEqual([f for f in pp.read("Karnstead", text) if f.layer == CULTURE], [])

    def test_ruins_and_old_places_are_once(self) -> None:
        self.assertEqual(
            pp.read("Velmoor", "Beyond lie the old elven ruins of Velmoor."),
            [Found(CULTURE, "elf", "once")],
        )
        self.assertEqual(
            pp.read("Velmoor", "Velmoor, an ancient elven city, is empty now."),
            [Found(CULTURE, "elf", "once")],
        )

    def test_a_quarter_is_part_of_the_place(self) -> None:
        self.assertEqual(
            pp.read("Harrowmere", "She keeps a shop in the gnomish quarter of Harrowmere."),
            [Found(CULTURE, "gnome", "strong", "the gnomish quarter")],
        )

    def test_a_characters_own_lineage_never_makes_a_places_culture(self) -> None:
        # (Supervisor's named test: one dwarf living somewhere doesn't make it dwarven)
        for text in (
            "Brewner, a dwarven smith of Karnstead, mends the gates.",
            "The dwarf Brewner lives in Karnstead.",
            "Karnstead, where a dwarven smith works, is quiet.",
            "Karnstead is home to Brewner, a dwarf.",
        ):
            with self.subTest(text):
                self.assertEqual(pp.read("Karnstead", text), [])


class Population(unittest.TestCase):
    def test_who_lives_there_with_its_share(self) -> None:
        cases = {
            "Bree, home to many halflings, sits by the road.": ("halfling", "many"),
            "Bree is mostly halflings.": ("halfling", "most"),
            "Bree, populated mainly by humans, trades wool.": ("human", "most"),
            "Bree is almost entirely gnomes.": ("gnome", "all"),
            "Bree, home to a few elves, is small.": ("elf", "few"),
        }
        for text, (who, share) in cases.items():
            with self.subTest(text):
                self.assertEqual(pp.read("Bree", text), [Found(POPULATION, who, share)])

    def test_no_share_said_keeps_none_and_never_a_number(self) -> None:
        self.assertEqual(
            pp.read("Bree", "Bree, populated by humans, trades wool."),
            [Found(POPULATION, "human", "")],
        )
        self.assertEqual(
            pp.read("Bree", "Bree, a town of 2,000 dwarves, trades iron."),
            [Found(POPULATION, "dwarf", "")],
        )

    def test_both_layers_from_one_sentence(self) -> None:
        self.assertEqual(
            pp.read("Karnstead", "The dwarven hold of Karnstead is home to many gnomes."),
            [Found(CULTURE, "dwarf", "strong"), Found(POPULATION, "gnome", "many")],
        )

    def test_a_phrase_about_somewhere_else_says_nothing(self) -> None:
        for text in (
            "Bree trades with Tunnelhold, home to many dwarves.",
            "Some halflings came to Bree last year.",
            "Bree sent mostly humans to the war.",
            # (a comma then a share and a plural is the next clause's subject, not who lives there)
            "After the battle at Bree, many dwarves fled north.",
            "Near Bree, a few elves were seen.",
            "The road from Bree, mostly humans walk it.",
        ):
            with self.subTest(text):
                self.assertEqual(pp.read("Bree", text), [])


class NamedCultures(unittest.TestCase):
    """A culture the story names (#4257 slice 3b; Supervisor's ruling): the culture shapes with a
    capitalised name, never a stop-list word, an entry's name or a lineage."""

    def test_the_shapes_give_a_named_culture(self) -> None:
        cases = {
            ("Luskan", "Luskan, an Illuskan port on the coast."): ("Illuskan", "strong", ""),
            ("Luskan", "the Illuskan city of Luskan rose there."): ("Illuskan", "strong", ""),
            ("Ascore", "the old Netherese ruins of Ascore lie buried."): ("Netherese", "once", ""),
            ("Waterdeep", "the Illuskan quarter of Waterdeep."): (
                "Illuskan", "strong", "the Illuskan quarter",
            ),
        }  # fmt: skip
        for (name, text), (culture, how, part) in cases.items():
            with self.subTest(text):
                self.assertEqual(
                    pp.read(name, text), [Found(CULTURE, "", how, part, culture=culture)]
                )

    def test_ordinary_words_entry_names_and_small_letters_say_nothing(self) -> None:
        for text, known in (
            ("the Northern town of Bree", ()),  # (a stop-list word, as the ruling names)
            ("the Great city of Bree", ()),
            ("the Old town of Bree", ()),
            ("the Harpers town of Bree", ("Harpers",)),  # (an entry the campaign knows)
            ("the illuskan town of Bree", ()),  # (not capitalised)
            ("the Illuskan smith of Bree", ()),  # (a person, never the place's culture)
            ("The Market Town of Bree", ()),  # (a heading's capitals: the noun is capitalised)
            ("the Market town of Bree", ()),
            ("the Dock ward of Bree", ()),
            ("the Smuggler's town of Bree", ()),  # (whose, never a culture)
        ):
            with self.subTest(text):
                self.assertEqual(pp.read("Bree", text, known), [])

    def test_a_lineage_word_stays_a_lineage(self) -> None:
        self.assertEqual(
            pp.read("Bree", "the Dwarven hold of Bree"), [Found(CULTURE, "dwarf", "strong")]
        )

    def test_the_stop_list_holds_no_name_and_its_key_is_the_migrations(self) -> None:
        self.assertIn("northern", pp.STOP)
        self.assertNotIn("illuskan", pp.STOP)
        self.assertEqual(pp.culture_key("  Illuskan \t folk "), "illuskan folk")
        self.assertEqual(pp.culture_name(" Illuskan  folk"), "Illuskan folk")


class Words(unittest.TestCase):
    def test_plain_adjectives_and_plurals_never_species(self) -> None:
        self.assertEqual(pp.words_of("dwarf", plural=False), "dwarven")
        self.assertEqual(pp.words_of("halfling", plural=True), "halflings")
        self.assertEqual(pp.words_of(pp.OTHER, "duergar", plural=True), "duergar")
        every = " ".join(" ".join(w) for w in pp.LINEAGES.values())
        self.assertNotIn("species", every)
        self.assertNotIn("lineage", every)

    def test_share_words_go_before_the_group(self) -> None:
        """Supervisor on #4413: "almost entirely / mostly / many / some / a few halflings", or
        just "halflings" with no share."""
        said = {how: f"{pp.HOW_WORDS[how]} halflings" for how in pp.POPULATION_HOWS}
        self.assertEqual(
            said,
            {"all": "almost entirely halflings", "most": "mostly halflings",
             "many": "many halflings", "some": "some halflings", "few": "a few halflings"},
        )  # fmt: skip


if __name__ == "__main__":
    unittest.main()
