"""Noticing a spell, condition or creature named at the table (#931): pure, on the shipped
index. Whole names only, everyday words skipped without their lead-in, plurals, the words
heard, and the list of everyday words checked against the index."""

from __future__ import annotations

import time
import unittest

from dmbot.rules import index
from dmbot.rules.spotter import EVERYDAY, HEARD_WINDOW_MAX, Spotter


def spotter(target: str = "2024", fallback: str = "2014") -> Spotter:
    return Spotter.from_pool(index.srd().names_pool(target, fallback))


def found(line: str, **kw: str) -> list[str]:
    return [m.entry.name for m in spotter(**kw).find(line)]


class Names(unittest.TestCase):
    def test_a_name_said_is_found(self) -> None:
        self.assertEqual(found("I cast Fireball at the door"), ["Fireball"])
        self.assertEqual(found("so he reads fireball scrolls"), ["Fireball"])  # case ignored

    def test_the_longest_name_wins(self) -> None:
        self.assertEqual(found("Hold Person on the ogre please"), ["Hold Person", "Ogre"])
        self.assertEqual(found("an Adult Red Dragon lands"), ["Adult Red Dragon"])

    def test_a_name_is_never_found_inside_another_word(self) -> None:
        self.assertEqual(found("the fireballoon and the ogres"), ["Ogre"])
        self.assertEqual(found("Gatekeeper"), [])

    def test_a_plural_is_the_same_name(self) -> None:
        self.assertEqual(found("three goblins and some ogres"), ["Goblin Warrior", "Ogre"])
        self.assertEqual(found("two Fireballs hit"), ["Fireball"])
        self.assertEqual(found("wolves everywhere"), [])  # an everyday word, not cast

    def test_an_apostrophe_and_a_hyphen_do_not_hide_a_name(self) -> None:
        self.assertEqual(found("Melf's acid arrow, then Tasha's hideous laughter"),
                         ["Acid Arrow", "Hideous Laughter"])  # fmt: skip
        self.assertEqual(found("a will-o'-wisp drifts"), ["Will-o’-Wisp"])

    def test_an_older_name_finds_the_newer_entry_and_says_what_was_said(self) -> None:
        (mention,) = spotter().find("the goblin attacks")
        self.assertEqual(mention.entry.name, "Goblin Warrior")
        self.assertEqual(mention.said, "goblin")

    def test_the_campaigns_rulesets_decide(self) -> None:
        old = [m.entry for m in spotter("2014", "none").find("the goblin and an Orc")]
        self.assertEqual([(e.name, e.edition) for e in old], [("Goblin", "2014"), ("Orc", "2014")])
        new = [m.entry for m in spotter("2024", "none").find("the goblin and an Orc")]
        self.assertEqual([(e.name, e.edition) for e in new], [("Goblin Warrior", "2024")])

    def test_one_entry_once_a_line(self) -> None:
        self.assertEqual(found("Fireball, Fireball, more Fireball"), ["Fireball"])
        self.assertEqual(found("goblin goblin Goblin Warrior"), ["Goblin Warrior"])

    def test_nothing_to_find(self) -> None:
        self.assertEqual(found(""), [])
        self.assertEqual(found("   ...  "), [])
        self.assertEqual(found("we walk to the inn and eat"), [])


class EverydayWords(unittest.TestCase):
    def test_they_are_skipped_alone(self) -> None:
        for line in (
            "a light in the dark", "I can fly there", "raise your shield", "a bat flew by",
            "the cat sleeps", "he fell prone", "the rat ran", "I wish it would end",
            "give me guidance", "that's an order, a command",
        ):  # fmt: skip
            with self.subTest(line):
                self.assertEqual(found(line), [])

    def test_cast_casts_and_casting_bring_them_back(self) -> None:
        for lead in ("cast", "casts", "casting", "Casts"):
            with self.subTest(lead):
                self.assertEqual(found(f"she {lead} light on it"), ["Light"])
        self.assertEqual(found("I cast Shield"), ["Shield"])
        self.assertEqual(found("he casts bless"), ["Bless"])

    def test_is_brings_back_a_condition_not_a_spell(self) -> None:
        self.assertEqual(found("the goblin is grappled"), ["Goblin Warrior", "Grappled"])
        self.assertEqual(found("they are poisoned"), ["Poisoned"])
        self.assertEqual(found("it gets stunned"), [])  # only is, are, was and were
        self.assertEqual(found("he got stunned by the news"), [])
        self.assertEqual(found("they were stunned"), ["Stunned"])
        self.assertEqual(found("the light is on"), [])  # "is" doesn't bring back a spell
        self.assertEqual(found("is light"), [])
        self.assertEqual(found("is Grappled"), ["Grappled"])

    def test_the_lead_in_must_sit_right_before(self) -> None:
        self.assertEqual(found("I cast a light"), [])
        self.assertEqual(found("cast the light spell"), [])
        self.assertEqual(found("grappled, he is"), [])

    def test_a_name_that_is_not_an_everyday_word_needs_no_lead_in(self) -> None:
        self.assertEqual(found("Hold Person"), ["Hold Person"])  # kept, as decided
        self.assertEqual(found("the Tarrasque"), ["Tarrasque"])
        self.assertEqual(found("Magic Missile"), ["Magic Missile"])
        self.assertEqual(found("a Beholder appears"), [])  # not in the 2024 free rules at all

    def test_plain_words_the_book_also_uses_as_names_give_no_false_cards(self) -> None:
        for line in (
            "what a nightmare", "a tough boss fight", "Battle stations", "that was a hard maze",
            "a veteran of the war", "the clone is bad", "a solar flare", "time stop the clock",
            "we have foresight", "my sprite sheet", "fabricate a story", "a unicorn horn",
        ):  # fmt: skip
            with self.subTest(line):
                self.assertEqual(found(line), [])

    def test_a_creature_with_an_everyday_name_never_cards_from_the_table(self) -> None:
        for line in ("cast Wolf", "the wolves howl", "casts bat", "it is a nightmare", "a Guard"):
            with self.subTest(line):
                self.assertEqual(found(line), [])
        self.assertEqual(found("the ogre and an owlbear"), ["Ogre", "Owlbear"])  # not everyday

    def test_a_name_inside_a_longer_word_is_nothing(self) -> None:
        for line in ("Fireballoon", "unwebbed", "Hold Personally", "ogresque", "Sleeping"):
            with self.subTest(line):
                self.assertEqual(found(line), [])

    def test_every_listed_word_is_really_a_name_in_the_index(self) -> None:
        names = {e.name for e in index.srd().entries}
        self.assertEqual(sorted(EVERYDAY - names), [])  # a typo in the list can't hide

    def test_each_listed_word_is_skipped_alone_and_a_spell_or_condition_is_found_with_its_lead_in(
        self,
    ) -> None:
        pool = index.srd().names_pool("2024", "2014")
        sp = spotter()
        for name in sorted(EVERYDAY):
            if index.normalize(name) not in pool:
                continue  # a 2014-only name has nothing to find in the 2024 rules
            kind = pool[index.normalize(name)].kind
            with self.subTest(name):
                self.assertEqual([m.entry.name for m in sp.find(f"the {name} here")], [])
                if kind == "monster":
                    self.assertEqual(sp.find(f"he casts {name}"), [])  # never, whatever precedes
                else:
                    lead = "is" if kind == "condition" else "cast"
                    self.assertTrue(sp.find(f"he {lead} {name}"), name)


class Heard(unittest.TestCase):
    def test_the_words_around_the_name_are_shown(self) -> None:
        (mention,) = spotter().find("so then I cast Fireball at the goblin door really")[:1]
        self.assertEqual(mention.said, "Fireball")
        self.assertEqual(mention.heard, "…then I cast Fireball at the…")

    def test_a_short_line_is_shown_whole(self) -> None:
        (mention,) = spotter().find("casts Bless")
        self.assertEqual(mention.heard, "casts Bless")

    def test_a_very_long_name_or_line_is_cut(self) -> None:
        (mention,) = spotter().find("x" * 200 + " Fireball " + "y" * 200)
        self.assertLessEqual(len(mention.heard), HEARD_WINDOW_MAX + 2)
        self.assertIn("Fireball", mention.heard)

    def test_the_key_is_the_entry_not_the_way_it_was_said(self) -> None:
        a, b = spotter().find("goblin"), spotter().find("Goblin Warrior")
        self.assertEqual(a[0].key, b[0].key)
        self.assertEqual(a[0].key, ("monster", "goblin warrior"))


class Edges(unittest.TestCase):
    def test_a_name_at_the_very_start_or_end_of_a_line(self) -> None:
        self.assertEqual(found("Fireball"), ["Fireball"])
        self.assertEqual(found("Fireball at the door"), ["Fireball"])
        self.assertEqual(found("at the door, Fireball"), ["Fireball"])
        self.assertEqual(found("light"), [])  # everyday, alone, at the start: no lead-in
        self.assertEqual(found("cast light"), ["Light"])

    def test_a_curly_apostrophe_is_the_same_as_a_straight_one(self) -> None:
        self.assertEqual(found("Tasha’s hideous laughter"), ["Hideous Laughter"])
        self.assertEqual(found("Tasha's hideous laughter"), ["Hideous Laughter"])

    def test_a_plural_of_a_name_already_seen_is_not_a_second_mention(self) -> None:
        self.assertEqual(found("Fireball then fireballs"), ["Fireball"])

    def test_the_names_are_built_once_for_each_pair_of_rulesets(self) -> None:
        from dmbot.dm_screen.rules_cards import spotter_for

        self.assertIs(spotter_for("2024", "2014"), spotter_for("2024", "2014"))
        self.assertIsNot(spotter_for("2024", "2014"), spotter_for("2014", "2024"))

    def test_a_long_line_with_many_repeats_stays_quick(self) -> None:
        sp = spotter()
        started = time.perf_counter()
        sp.find("goblin " * 500)
        sp.find("word " * 500)
        self.assertLess(time.perf_counter() - started, 0.5)  # ~40 ms in practice


class Speed(unittest.TestCase):
    def test_a_line_is_quick_to_look_through(self) -> None:
        sp = spotter()
        line = (
            "so then the goblin is grappled and I cast Fireball at the dragon while it flies away"
        )
        started = time.perf_counter()
        for _ in range(200):
            sp.find(line)
        self.assertLess(
            (time.perf_counter() - started) / 200, 0.05
        )  # far above ~0.3 ms: CI can be slow
        self.assertLess(len(sp.by_first), 5000)


if __name__ == "__main__":
    unittest.main()
