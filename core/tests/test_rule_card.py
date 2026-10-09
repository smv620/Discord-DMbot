"""The rule card (#908): built without Discord, from the shipped index. Its facts line, its
source, the legacy and renamed notes, house rules first, long text split without cutting
a word, and the no-match words."""

from __future__ import annotations

import re
import unittest

from dmbot.rules import index
from dmbot.rules.house import HouseRule
from dmbot.ui import rule_card
from dmbot.ui.rule_card import PART_MAX

NOW = 1_700_000_000


def rule(n: int, text: str, instead: str | None = None, cid: str = "c1") -> HouseRule:
    return HouseRule(n, cid, text, instead, None, None, 7, NOW + n, NOW + n)


def hit(name: str, target: str = "2024", fallback: str = "2014") -> index.Hit:
    found = index.srd().lookup(name, target, fallback)
    assert found is not None, name
    return found


class Facts(unittest.TestCase):
    def test_a_spell(self) -> None:
        line = rule_card.facts(hit("Fireball").entry)
        self.assertEqual(
            line,
            "3rd-level Evocation · Casting time: Action · Range: 150 feet · "
            "Components: V, S, M (a ball of bat guano and sulfur) · Duration: Instantaneous",
        )

    def test_a_cantrip_and_a_ritual(self) -> None:
        splash = rule_card.facts(hit("Acid Splash").entry)
        self.assertTrue(splash.startswith("Evocation cantrip"))
        detect = hit("Detect Magic", "2014", "none").entry
        self.assertIn("1st-level Divination (ritual)", rule_card.facts(detect))
        self.assertTrue(rule_card.facts(hit("Wish").entry).startswith("9th-level"))

    def test_a_creature(self) -> None:
        self.assertEqual(
            rule_card.facts(hit("Goblin Warrior").entry),
            "Small Fey (Goblinoid) · AC 15 · HP 10 (3d6) · Speed 30 ft. · CR 1/4",
        )

    def test_a_condition_has_none(self) -> None:
        self.assertEqual(rule_card.facts(hit("Grappled").entry), "")


class Header(unittest.TestCase):
    def test_the_source_is_always_there(self) -> None:
        text = rule_card.header(hit("Fireball"), "fireball", [])
        self.assertIn("📖 **Fireball** (spell)", text)
        self.assertIn("_Source: SRD 5.2.1, Spell Descriptions, p. 131_", text)
        self.assertIn("the free rules (SRD)", text)
        self.assertIn("you decide what applies", text)

    def test_an_exact_name_says_nothing_extra(self) -> None:
        text = rule_card.header(hit("Fireball"), "  FIREBALL ", [])
        self.assertNotIn("you typed", text)
        self.assertNotIn("now called", text)
        self.assertNotIn("older", text)

    def test_the_older_rules_are_tagged_and_said_plainly(self) -> None:
        text = rule_card.header(hit("Orc"), "Orc", [])
        self.assertIn("SRD 5.1, Monsters, p. 339 [Legacy 2014]", text)
        self.assertIn("From the older 2014 rules (the newer free rules don't have it).", text)

    def test_the_older_rules_note_only_when_they_were_the_fallback(self) -> None:
        note = rule_card.OLDER_NOTE
        # 2014 is the campaign's own choice: the tag in the source line is enough
        own = hit("Fireball", "2014", "2024")
        text = rule_card.header(own, "Fireball", [])
        self.assertIn("SRD 5.1, Spell Descriptions", text)
        self.assertIn("[Legacy 2014]", text)
        self.assertNotIn(note, text)
        self.assertNotIn(note, rule_card.header(hit("Fireball", "2014", "none"), "Fireball", []))
        # a newer entry that came from the fallback is not "older" and says "newer"
        newer = rule_card.header(hit("Goblin Warrior", "2014", "2024"), "Goblin Warrior", [])
        self.assertNotIn(note, newer)
        self.assertNotIn("older", newer.lower().replace("the older rules", ""))
        self.assertIn("[Newer 2024 rules]", newer)
        self.assertNotIn("[2024]", newer)
        # a 2014 entry the newer rules lack, found in the fallback: the note
        self.assertIn(note, rule_card.header(hit("Orc", "2024", "2014"), "Orc", []))
        # the target's own newest rules need neither
        plain = rule_card.header(hit("Fireball", "2024", "2014"), "Fireball", [])
        self.assertNotIn(note, plain)
        self.assertNotIn("[", plain.replace("[Legacy", "").split("_Source")[1].split("_")[0])

    def test_an_older_name_says_what_it_is_called_now(self) -> None:
        text = rule_card.header(hit("Goblin"), "goblin", [])
        self.assertIn("📖 **Goblin Warrior** (creature)", text)
        self.assertIn("_goblin is now called Goblin Warrior in the newer rules._", text)
        self.assertNotIn("[Legacy 2014]", text)

    def test_another_way_of_saying_it_is_told_not_called_a_rename(self) -> None:
        text = rule_card.header(hit("Deep Gnome", "2014", "none"), "Deep Gnome", [])
        self.assertIn("_Showing Gnome, Deep (Svirfneblin) (you typed Deep Gnome)._", text)
        self.assertNotIn("is now called", text)

    def test_what_the_dm_typed_never_formats_the_card(self) -> None:
        text = rule_card.header(hit("Goblin"), "**Goblin** @everyone", [])
        self.assertNotIn("**Goblin** @everyone", text)
        self.assertIn("\\*\\*Goblin\\*\\*", text)


class HouseRules(unittest.TestCase):
    def test_a_rule_that_names_the_thing_comes_first(self) -> None:
        rules = [rule(12, "Fireball also lights the caster's hair", "Fireball only damages")]
        text = rule_card.header(hit("Fireball"), "fireball", rules)
        self.assertTrue(text.startswith("🏠 **House rule 12:** Fireball also lights"))
        self.assertIn("(instead of: Fireball only damages)", text)
        self.assertLess(text.index("🏠"), text.index("📖"))

    def test_matching_ignores_case_and_punctuation_and_needs_the_whole_name(self) -> None:
        rules = [
            rule(1, "FIREBALL, in a cave, is worse"),
            rule(2, "Fire damage is rolled twice"),
            rule(3, "No firebolts indoors"),
            rule(4, "Melf’s acid arrow splashes", None),
        ]
        names = rule_card.names_for(hit("Fireball").entry, "fireball")
        self.assertEqual([r.number for r in rule_card.house_matches(rules, names)], [1])
        acid = rule_card.names_for(hit("Melf's Acid Arrow").entry, "Melf's Acid Arrow")
        self.assertEqual([r.number for r in rule_card.house_matches(rules, acid)], [4])

    def test_a_creature_is_matched_by_the_name_typed_too(self) -> None:
        rules = [rule(5, "Goblins always flee at half HP, as the Goblin does")]
        names = rule_card.names_for(hit("Goblin").entry, "Goblin")
        self.assertEqual([r.number for r in rule_card.house_matches(rules, names)], [5])

    def test_only_the_rules_given_are_ever_shown(self) -> None:
        mine = [rule(1, "Fireball is louder", cid="c1")]
        text = rule_card.header(hit("Fireball"), "fireball", mine)
        self.assertIn("House rule 1", text)
        other = rule_card.header(hit("Fireball"), "fireball", [])  # another campaign's: none given
        self.assertNotIn("🏠", other)

    def test_a_few_are_shown_and_the_rest_counted(self) -> None:
        many = [rule(n, "Fireball is louder") for n in range(1, 7)]
        text = rule_card.header(hit("Fireball"), "fireball", many)
        self.assertEqual(text.count("🏠 **House rule"), rule_card.HOUSE_SHOWN)
        self.assertIn(
            "…and 3 more house rules mention this. See them all with `/dmbot houserules`.", text
        )

    def test_the_dms_words_never_format_the_card(self) -> None:
        text = rule_card.header(hit("Fireball"), "fireball", [rule(1, "Fireball **bold** ||x||")])
        self.assertIn("\\*\\*bold\\*\\*", text)
        self.assertIn("\\|\\|x\\|\\|", text)

    def test_a_very_long_rule_is_cut_whole(self) -> None:
        text = rule_card.house_lines([rule(1, "Fireball " + "*" * 600)])[0]
        self.assertLessEqual(len(text), rule_card.HOUSE_LINE_MAX)
        self.assertTrue(text.endswith("…"))
        self.assertFalse(text.endswith("\\…"))


class Splitting(unittest.TestCase):
    def test_short_text_is_one_part(self) -> None:
        self.assertEqual(rule_card.split_text("a\nb", 100, 100), ["a\nb"])
        self.assertEqual(rule_card.split_text("", 100, 100), [""])

    def test_parts_fit_and_end_between_paragraphs_when_they_can(self) -> None:
        text = "\n".join(f"Paragraph {n} " + "word " * 30 for n in range(10))
        parts = rule_card.split_text(text, 400, 600)
        self.assertGreater(len(parts), 1)
        self.assertLessEqual(len(parts[0]), 400)
        self.assertTrue(all(len(p) <= 600 for p in parts[1:]))
        self.assertTrue(all(p.rstrip().endswith("word") for p in parts))
        self.assertEqual("\n".join(parts), text)  # nothing lost, nothing added

    def test_a_long_paragraph_is_split_between_sentences_then_words(self) -> None:
        text = " ".join(f"This is sentence number {n}." for n in range(60))
        parts = rule_card.split_text(text, 300, 300)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertEqual(" ".join(parts), text)
        self.assertTrue(all(p.endswith(".") for p in parts))
        blob = "x" * 50 + " " + "word " * 200
        pieces = rule_card.split_text(blob, 120, 120)
        self.assertTrue(all(len(p) <= 120 for p in pieces))
        self.assertEqual(" ".join(pieces).split(), blob.split())  # no word cut

    def test_a_break_inside_a_paragraph_is_a_space_between_paragraphs_a_new_line(self) -> None:
        text = "First paragraph. " + "word " * 80 + "\nSecond paragraph here."
        parts = rule_card.split_text(text, 120, 120)
        self.assertGreater(len(parts), 2)
        self.assertEqual(" ".join(parts).split(), text.split())  # nothing lost
        # a line break appears only where the text had one, never inside a long paragraph
        for part in parts:
            if "\n" in part:
                self.assertEqual(part.count("\n"), 1)
                self.assertTrue(part.endswith("Second paragraph here."))

    def test_a_word_longer_than_a_part_is_the_only_thing_cut(self) -> None:
        parts = rule_card.split_text("y" * 250, 100, 100)
        self.assertEqual([len(p) for p in parts], [100, 100, 50])

    def test_every_entry_of_the_index_makes_a_card_that_fits_and_loses_nothing(self) -> None:
        srd = index.srd()
        longest = 0
        for e in srd.entries:
            found = srd.lookup(e.name, e.edition, "none")
            assert found is not None
            parts = rule_card.card_parts(found, e.name, [rule(1, f"{e.name} is loud")])
            with self.subTest(e.name, edition=e.edition):
                self.assertTrue(all(len(p) <= 2000 for p in parts))
                longest = max(longest, *(len(p) for p in parts))
                body = "\n".join(p.split("\n", 1)[1] if i else p for i, p in enumerate(parts))
                squeezed = re.sub(r"\s+", "", body)
                for word in re.findall(r"\S+", rule_card._md(e.text))[
                    :: max(1, len(e.text) // 200)
                ]:
                    self.assertIn(re.sub(r"\s+", "", word), squeezed)
        self.assertLessEqual(longest, PART_MAX)

    def test_a_long_creature_comes_in_parts_each_with_its_name(self) -> None:
        found = hit("Vampire", "2014", "none")
        parts = rule_card.card_parts(found, "Vampire", [])
        self.assertGreater(len(parts), 2)
        self.assertIn("📖 **Vampire** (part 2 of ", parts[1])
        self.assertIn(f"(part {len(parts)} of {len(parts)})", parts[-1])

    def test_a_huge_house_rule_list_still_leaves_room_for_the_text(self) -> None:
        rules = [rule(n, "Fireball " + "*" * 500, "*" * 500) for n in range(1, 9)]
        parts = rule_card.card_parts(hit("Fireball"), "f" * 100, rules)
        self.assertTrue(all(len(p) <= 2000 for p in parts))
        self.assertIn("A bright streak flashes", "".join(parts))


class Worst(unittest.TestCase):
    def test_the_heading_never_fills_a_message(self) -> None:
        # The longest facts line, many long house rules, a long name typed: still fits.
        rules = [rule(n, "Vampire " + "*" * 500, "*" * 500) for n in range(1, 9)]
        for name in ("Vampire", "Wish", "Ancient Red Dragon", "Gnome, Deep (Svirfneblin)"):
            found = hit(name, "2014", "none") if name != "Wish" else hit(name)
            parts = rule_card.card_parts(
                found, "*" * 100, [*rules, rule(9, f"{name} " + "*" * 500)]
            )
            with self.subTest(name):
                self.assertTrue(all(len(p) <= 2000 for p in parts))
                self.assertGreaterEqual(len(parts[0]) - len(rule_card.header(found, "x", [])), 0)

    def test_house_rules_are_cut_shorter_then_only_counted(self) -> None:
        rules = [rule(n, "Fireball " + "*" * 400, "*" * 400) for n in range(1, 4)]
        full = rule_card.house_lines(rules)
        short = rule_card.house_lines(rules, 60)
        none = rule_card.house_lines(rules, 0)
        self.assertTrue(all(len(x) <= 60 for x in short))
        self.assertGreater(len(full[0]), len(short[0]))
        self.assertEqual(
            none, ["🏠 3 house rules mention this. See them all with `/dmbot houserules`."]
        )
        self.assertEqual(rule_card.house_lines([], 0), [])


class NoMatch(unittest.TestCase):
    def test_it_says_so_plainly_and_nothing_is_guessed(self) -> None:
        text = rule_card.no_match_text("Frobnicate", [], False)
        self.assertIn("DMbot couldn't find **Frobnicate** in the free rules (SRD).", text)
        self.assertIn("Only the free rules are in DMbot so far, not your own books.", text)
        self.assertIn("Check the spelling", text)
        self.assertNotIn("Did you mean", text)

    def test_with_suggestions_it_asks(self) -> None:
        text = rule_card.no_match_text("Firball", [], True)
        self.assertIn("**Did you mean…?**", text)

    def test_a_house_rule_that_names_it_still_shows(self) -> None:
        text = rule_card.no_match_text(
            "Kraken's Kiss", [rule(3, "The Kraken's Kiss is a house spell")], False
        )
        self.assertTrue(text.startswith("🏠 **House rule 3:**"))

    def test_what_was_typed_never_formats_it(self) -> None:
        text = rule_card.no_match_text("**x** @everyone", [], False)
        self.assertIn("\\*\\*x\\*\\*", text)


class Names(unittest.TestCase):
    def test_a_choice_says_what_kind_and_whether_it_is_older(self) -> None:
        self.assertEqual(rule_card.choice_label(hit("Fireball").entry), "Fireball (spell)")
        self.assertEqual(rule_card.choice_label(hit("Orc").entry), "Orc (creature) [Legacy 2014]")
        self.assertLessEqual(
            len(rule_card.choice_label(hit("Gnome, Deep (Svirfneblin)").entry)), 100
        )


class Suggestions(unittest.TestCase):
    def test_close_names_are_offered_never_picked(self) -> None:
        srd = index.srd()
        self.assertEqual([e.name for e in srd.suggest("Firball", "2024", "2014")][:1], ["Fireball"])
        self.assertIsNone(srd.lookup("Firball", "2024", "2014"))  # still no match
        self.assertEqual(srd.suggest("", "2024", "2014"), [])
        self.assertEqual(srd.suggest("xyzzyplugh", "2024", "2014"), [])

    def test_at_most_five_newest_rules_first(self) -> None:
        found = index.srd().suggest("wall", "2024", "2014")
        self.assertEqual(len(found), 5)
        self.assertTrue(all(e.edition == "2024" for e in found))

    def test_the_typeahead_begins_with_what_is_typed_and_has_at_most_25(self) -> None:
        srd = index.srd()
        found = srd.typeahead("fire", "2024", "2014")
        self.assertTrue(found[0].name.lower().startswith("fire"))
        self.assertLessEqual(len(found), 25)
        self.assertEqual(len(srd.typeahead("", "2024", "2014")), 25)
        self.assertTrue(all(e.edition == "2024" for e in srd.typeahead("", "2024", "2014")))
        self.assertIn("Orc", [e.name for e in srd.typeahead("orc", "2024", "2014")])
        self.assertNotIn("Orc", [e.name for e in srd.typeahead("orc", "2024")])  # no fallback


if __name__ == "__main__":
    unittest.main()
