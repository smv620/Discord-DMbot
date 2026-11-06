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
            ("the Sunbrand sword", "name"), ("Moonblade", "name"),
            ("Orb of Dragonkind", "name"), ("Cerra's ring", "owner"),
            ("Cerra\u2019s ring", "owner"), ("Garret's boots", "owner"),
            ("Mira's longbow", "owner"),
        ]:  # fmt: skip
            verdict = unique_item.judge(name)
            self.assertEqual((verdict.keep, verdict.why), (True, why), name)

    def test_ordinary_gear_and_quantities_are_not_tracked(self) -> None:
        for name in ("a dagger", "dagger", "a longbow", "arrows", "Mira's arrows", "rations",
                     "a sled", "sled", "two sleds and eight dogs", "Dog sleds", "Wyvern poison",
                     "a vial of poison", "a ring of iron keys", "tome", "a tome",
                     "two potions of healing", "Waterskin", "the guard's sword", "Iron sword",
                     "Wyvern poison", "his sled", "rope"):  # fmt: skip
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

    def test_a_story_word_in_another_sentence_says_nothing_about_the_item(self) -> None:
        far = (
            "Tavin buys a dagger at the market. "
            + "The town was quiet that day. " * 8
            + "Later the party talks about the plan to find the villain's plan."
        )
        self.assertFalse(unique_item.keeps("a dagger", far))
        near = (
            "Tavin buys a dagger. "
            + "The town was quiet that day. " * 8
            + "The dagger was stolen from the shrine."
        )
        self.assertTrue(unique_item.keeps("a dagger", near))
        # and the plain words (plan, quest, forge) alone do not make an item a story
        self.assertFalse(unique_item.keeps("a dagger", "Tavin has a dagger for the quest"))

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

    def test_an_item_is_kept_if_any_of_its_spellings_is_one_particular_thing(self) -> None:
        from collections import Counter

        from dmbot.memory import background as bg

        lost: Counter[str] = Counter()
        names = [
            bg.Found("the sword", "item", ("Moonblade",), (), "the sword"),
            bg.Found("a dagger", "item", (), (), "a dagger"),
            bg.Found("a dagger", "item", (), (), "a dagger"),  # said again: counted once
        ]
        kept = bg.merge([names], dropped=lost)
        self.assertEqual([f.name for f in kept], ["the sword"])
        self.assertEqual(lost["item_not_unique"], 1)
        self.assertEqual(len(bg.merge([names])), 1)  # (no counter given: nothing breaks)

    def test_a_story_quote_keeps_what_the_bare_name_would_drop(self) -> None:
        from dmbot.memory import background as bg

        found = bg.Found("ore bins", "item", (), (), "the ore bins meant for Xardorok")
        self.assertEqual([f.name for f in bg.merge([[found]])], ["ore bins"])

    def test_the_readers_are_told_the_same_rule(self) -> None:
        from dmbot.memory import lore_read
        from dmbot.memory.extractor import SYSTEM

        self.assertIn("Leave out ordinary gear", SYSTEM)
        self.assertIn("one particular thing", lore_read.read_instructions())

    def test_the_examples_teach_unique_items_only(self) -> None:
        import inspect

        from dmbot.memory import ontology, rel_golden, rel_type_texts

        text = "".join(inspect.getsource(m) for m in (ontology, rel_type_texts, rel_golden))
        for generic in ("carries a longbow", "bears a sealed letter", "ring of iron keys",
                        "carved wooden holy symbol", "has the longbow"):  # fmt: skip
            self.assertNotIn(generic, text)


if __name__ == "__main__":
    unittest.main()


class MarkedByTheText(unittest.TestCase):
    """The text says it is one of a kind (found on the sample lore: the wolf figurine)."""

    def test_a_unique_or_inlaid_or_runed_thing_is_one_particular_thing(self) -> None:
        said = (
            "He carries a unique wolf figurine inlaid with black gemstones, etched with Sylvan "
            "runes of conjuration."
        )
        verdict = unique_item.judge("wolf figurine", said)
        self.assertEqual((verdict.keep, verdict.why), (True, "marked"))
        self.assertFalse(unique_item.judge("wolf figurine").keep)  # said alone it is ordinary

    def test_a_mark_in_another_sentence_or_for_gear_that_is_plainly_ordinary_is_not_enough(
        self,
    ) -> None:
        elsewhere = "The runes on the door glowed. " + "The party carried rations for a week. " * 8
        self.assertFalse(unique_item.judge("rations", elsewhere).keep)
        self.assertFalse(unique_item.judge("a sled", "The two sleds were fast.").keep)

    def test_a_mark_on_the_neighbouring_item_does_not_make_the_plain_one_unique(self) -> None:
        said = (
            "The company bolstered their provisions with waterproof backpacks and a Potion of "
            "Healing, while Claude sold a wolf figurine etched with conjuration runes to Belleros."
        )
        self.assertFalse(unique_item.judge("waterproof backpacks", said).keep)
        self.assertTrue(unique_item.judge("wolf figurine", said).keep)


class StandardRulebookItems(unittest.TestCase):
    """An item the rulebook holds is one of many in the world, like any gear (#3379)."""

    def test_a_standard_item_is_not_tracked_by_its_name_alone(self) -> None:
        for name in ("Potion of Healing", "Scroll of Identify", "scroll of Detect Magic",
                     "Bag of Holding", "Rope of Climbing", "Cloak of Protection",
                     "Staff of Power", "Vorpal Sword"):  # fmt: skip
            verdict = unique_item.judge(name)
            self.assertEqual((verdict.keep, verdict.why), (False, "standard"), name)

    def test_an_owner_a_story_or_a_name_the_rules_do_not_hold_keeps_it(self) -> None:
        self.assertTrue(unique_item.judge("Mira's Bag of Holding").keep)
        self.assertTrue(unique_item.judge("the potion of healing the cult poisoned").keep)
        self.assertTrue(
            unique_item.judge("Potion of Healing", "The cult poisoned it before the heist.").keep
        )
        for name in ("Heartseeker", "Wand of Conducting", "Icy Rapier of Life Stealing",
                     "Lantern of Greater Tracking"):  # fmt: skip
            self.assertTrue(unique_item.judge(name).keep, name)
