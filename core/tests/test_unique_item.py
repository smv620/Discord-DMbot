"""Only unique items are tracked (#3373): "Garret's sled", not "a sled". A golden table of cases,
all invented or from the public sample lore, checked one way and the other."""

from __future__ import annotations

import unittest

from dmbot.memory import unique_item


class Judging(unittest.TestCase):
    def test_a_proper_name_an_owner_or_a_description_is_one_particular_thing(self) -> None:
        for name, why in [
            ("Heartseeker", "name"), ("Aurora's Wind", "name"), ("Wand of Conducting", "name"),
            ("Icy Rapier of Life Stealing", "name"), ("the Widowmaker", "name"),
            ("Garret's sled", "owner"), ("Sparkwalker's tome", "owner"), ("Oyami's pipe", "owner"),
            ("the crystal from the mind flayer's skull", "description"),
        ]:  # fmt: skip
            verdict = unique_item.judge(name)
            self.assertEqual((verdict.keep, verdict.why), (True, why), name)

    def test_ordinary_gear_and_quantities_are_not_tracked(self) -> None:
        for name in ("a dagger", "dagger", "a longbow", "arrows", "Mira's arrows", "rations",
                     "a sled", "sled", "two sleds and eight dogs", "Dog sleds", "Wyvern poison",
                     "a vial of poison", "a ring of iron keys", "tome", "a tome"):  # fmt: skip
            self.assertFalse(unique_item.keeps(name), name)

    def test_a_dagger_later_named_is_tracked_from_then_on(self) -> None:
        self.assertFalse(unique_item.keeps("a dagger"))
        self.assertTrue(unique_item.keeps("the Widowmaker"))

    def test_the_story_turning_on_it_makes_it_one_thing_even_by_weight(self) -> None:
        # one consignment, on one ferry, meant for one villain: not a pile of common things
        for name, context in [
            ("Chardalyn shipment", ""),
            ("raw chardalyn", "1,500 pounds on the ferry, meant for Xardorok"),
            ("ore bins", "the party stole the ore bins to stop the plan"),
        ]:
            verdict = unique_item.judge(name, context)
            self.assertEqual((verdict.keep, verdict.why), (True, "story"), name)
        self.assertFalse(unique_item.keeps("ore bins", "the party walked past the ore bins"))

    def test_more_likely_than_not_decides_the_rest_and_no_one_is_asked(self) -> None:
        # kept: a kind of thing with some identity
        self.assertTrue(unique_item.keeps("Psi crystal"))
        self.assertTrue(unique_item.keeps("Heart of chardalyn"))
        self.assertTrue(unique_item.keeps("Wolf figurine", "the wolf figurine etched with runes"))
        # dropped: clear generic gear with nothing singling it out
        self.assertFalse(unique_item.keeps("Wyvern poison"))
        self.assertFalse(unique_item.keeps("a ring of iron keys"))
        self.assertFalse(unique_item.keeps("longbow", "Mira has the longbow in her hands"))

    def test_the_curse_that_follows_one_item_is_a_story(self) -> None:
        self.assertTrue(unique_item.keeps("Chardalyn spear", "the whispers of the chardalyn spear"))

    def test_a_possessive_of_a_pronoun_or_a_plural_is_not_an_owner(self) -> None:
        self.assertFalse(unique_item.keeps("his sled"))
        self.assertFalse(unique_item.keeps("the guard's dagger"))
        self.assertFalse(unique_item.keeps("Garret's sleds"))

    def test_an_empty_name_is_dropped(self) -> None:
        self.assertFalse(unique_item.keeps("   "))


class Applied(unittest.TestCase):
    def test_the_lore_list_leaves_out_a_generic_item_and_counts_it(self) -> None:
        from collections import Counter

        from dmbot.memory import background as bg

        names = [
            bg.Found("Heartseeker", "item", (), (), "Tavin carries Heartseeker"),
            bg.Found("a dagger", "item", (), (), "Tavin carries a dagger"),
            bg.Found("Garret's sled", "item", (), (), "Garret's sled"),
            bg.Found("Mira's arrows", "item", (), (), "Mira's arrows"),
            bg.Found("Aldmoor", "place", (), (), "in Aldmoor"),
        ]
        lost: Counter[str] = Counter()
        kept = bg.merge([names], dropped=lost)
        self.assertEqual([f.name for f in kept], ["Heartseeker", "Garret's sled", "Aldmoor"])
        self.assertEqual(lost["item_not_unique"], 2)  # (a count, never the names)

    def test_the_readers_are_told_the_same_rule(self) -> None:
        from dmbot.memory import lore_read
        from dmbot.memory.extractor import SYSTEM

        self.assertIn("Leave out ordinary gear", SYSTEM)
        self.assertIn("one particular thing", lore_read.read_instructions())

    def test_the_examples_teach_unique_items_only(self) -> None:
        from dmbot.memory import ontology, rel_golden, rel_type_texts

        text = repr(vars(ontology)) + repr(vars(rel_type_texts)) + repr(vars(rel_golden))
        for generic in ("carries a longbow", "bears a sealed letter", "ring of iron keys",
                        "carved wooden holy symbol", "has the longbow"):  # fmt: skip
            self.assertNotIn(generic, text)


if __name__ == "__main__":
    unittest.main()
