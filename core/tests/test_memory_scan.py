"""The after-session scan that suggests new names (#126, step 3), without a database."""

import unittest

from dmbot.devtools.stt_bakeoff.data import LINES, NAMES
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, PROPOSED, Alias, Entity, name_key
from dmbot.memory.scan import (
    MAX_SUGGESTIONS,
    Match,
    Suggestion,
    _many_word_names,
    find_new_names,
    group_alike,
    near_match_in,
    sound_keys,
)


def names(found: list[Suggestion]) -> list[str]:
    return [s.name for s in found]


class Scan(unittest.TestCase):
    def test_names_said_mid_sentence_twice_are_suggested(self) -> None:
        found = find_new_names(
            ["We ride to Bryn Shander at dawn.", "Bryn Shander is cold.", "I like Bryn Shander."]
        )
        self.assertEqual([(s.name, s.times) for s in found], [("Bryn Shander", 3)])

    def test_once_is_not_enough(self) -> None:
        self.assertEqual(find_new_names(["We ride to Targos at dawn."]), [])

    def test_sentence_starts_and_common_words_are_not_names(self) -> None:
        lines = [
            "Then we go. Then we rest.",
            "Okay, I roll. Okay, done.",
            "Roll for Insight. Roll it.",
        ]
        self.assertEqual(find_new_names(lines), [])

    def test_a_word_also_said_in_lower_case_is_not_a_name(self) -> None:
        lines = ["I hit the Wall. The Wall falls.", "Behind the wall is a door."]
        self.assertEqual(find_new_names(lines), [])

    def test_known_answers_are_never_suggested_again(self) -> None:
        lines = ["I see Hrothgar now.", "We ask Hrothgar again.", "So Mia laughs.", "I tell Mia."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar", "Mia"])  # without skips
        self.assertEqual(names(find_new_names(lines, skip_keys=["hrothgar", "Mia"])), [])

    def test_a_word_of_a_known_name_said_whole_is_not_a_new_name(self) -> None:
        # #399: Oskar Vane is known, and so is "Vane" on its own.
        skip = ["Oskar Vane", "Vane"]
        lines = ["Ilvaris and Oskar Vane meet in secret.", "Ilvaris and Oskar Vane meet in secret."]
        self.assertEqual(find_new_names(lines, skip_keys=skip), [])
        without = names(find_new_names(lines))  # no known names: the whole run is offered
        self.assertIn("Oskar Vane", without)

    def test_the_same_word_alone_is_still_offered(self) -> None:
        # A new "Oskar" could be someone else, so away from the known name it stays.
        skip = ["Oskar Vane", "Vane"]
        lines = [
            "Ilvaris and Oskar Vane meet in secret.",
            "Ilvaris and Oskar Vane meet in secret.",
            "Later we ask Oskar about the road.",
            "We trust Oskar now.",
        ]
        found = find_new_names(lines, skip_keys=skip)
        self.assertEqual([(s.name, s.times) for s in found], [("Oskar", 2)])  # only the lone ones

    def test_names_either_side_of_a_known_name_are_still_found(self) -> None:
        skip = ["Oskar Vane", "Vane"]
        lines = [
            "We find Brenn Oskar Vane Tolliver by night.",
            "Then Brenn Oskar Vane Tolliver runs.",
        ]
        found = names(find_new_names(lines, skip_keys=skip))
        self.assertIn("Brenn", found)
        self.assertIn("Tolliver", found)
        self.assertNotIn("Oskar", found)

    def test_edges_of_a_known_name_said_whole(self) -> None:
        skip = ["Oskar Vane", "Vane", "Vane Tolliver", "Ka'zeth Vane"]

        def found(*lines: str) -> list[str]:
            return names(find_new_names(list(lines) * 2, skip_keys=skip))

        self.assertEqual(found("We saw Oskar Vane's cat."), [])  # a possessive
        self.assertEqual(found("Oskar Vane and then Oskar Vane again."), [])  # said twice
        self.assertEqual(found("We follow Oskar Vane Tolliver home."), [])  # overlapping names
        self.assertEqual(found("Ask Oskar Vane Hrothgar."), ["Hrothgar"])  # at sentence start
        self.assertEqual(found("Oskar Vane meets us."), [])  # its tail "Vane" isn't offered
        self.assertEqual(found("We met Ka-Zeth Vane today."), [])  # spelled another way
        # A run of three before the known name is kept, since the name ends it.
        self.assertEqual(found("We met Alder Brook Fenn Oskar Vane."), ["Alder Brook Fenn"])

    def test_a_many_word_name_covers_its_words_even_if_each_word_is_unknown(self) -> None:
        lines = ["We met Caer Dineval today.", "Caer Dineval is far."]
        self.assertEqual(find_new_names(lines, skip_keys=["Caer Dineval"]), [])
        lone = ["We saw Caer today.", "We saw Caer again."]
        self.assertEqual(names(find_new_names(lone, skip_keys=["Caer Dineval"])), ["Caer"])

    def test_a_big_campaign_is_scanned_by_first_word_not_name_by_name(self) -> None:
        def word(i: int) -> str:  # names have letters only: "Qaab", "Qaac"...
            return "Qa" + "".join(chr(97 + (i // 26**k) % 26) for k in range(3))

        pairs = [f"{word(i)} Zz{word(i)}" for i in range(5000)]
        many = _many_word_names(k.casefold() for k in pairs)
        self.assertEqual(len(many), 5000)
        self.assertEqual(many[word(7).casefold()], [(f"zz{word(7)}".casefold(),)])
        lines = [f"We meet {pairs[42]} at dawn."] * 3 + ["We meet Hrothgar."] * 3
        self.assertEqual(names(find_new_names(lines, skip_keys=pairs)), ["Hrothgar"])

    def test_possessives_count_as_the_name(self) -> None:
        lines = ["I meet Hrothgar's men.", "We see Hrothgar’s axe.", "I ask Ka'zeth twice."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar"])

    def test_a_name_after_a_capitalized_first_word_is_found(self) -> None:
        lines = ["Ask Hrothgar now.", "Tell Hrothgar now."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar"])

    def test_long_runs_are_dropped_not_chopped(self) -> None:
        lines = ["We reach Caer Dineval Ice Fortress.", "Back to Caer Dineval Ice Fortress."]
        self.assertEqual(find_new_names(lines), [])

    def test_names_in_any_script(self) -> None:
        self.assertEqual(
            names(find_new_names(["Wir sehen Łódź heute.", "Dann Łódź wieder."])), ["Łódź"]
        )

    def test_classes_and_species_are_game_words(self) -> None:
        self.assertEqual(find_new_names(["I am a Paladin.", "My Paladin and the Elf."] * 2), [])

    def test_leading_words_are_trimmed_from_a_name(self) -> None:
        lines = ["They serve The Ashen Crown.", "Beware The Ashen Crown."]
        self.assertEqual(names(find_new_names(lines)), ["Ashen Crown"])

    def test_bakeoff_script_finds_its_invented_names(self) -> None:
        found = set(names(find_new_names([line.text for line in LINES], min_times=2)))
        self.assertLessEqual(len(found), MAX_SUGGESTIONS)
        invented = {n.canonical for n in NAMES}
        self.assertTrue(found <= invented | {"Ashen Crown"}, found - invented)  # nothing else
        self.assertIn("Cerric", found)


def near_match(lookup: CampaignLookup, name: str) -> Match | None:
    """What the review does: candidates as `MemoryStore.sound_alikes` picks them
    (confirmed, non-secret names of confirmed entries sharing a sound code)."""
    codes = set(sound_keys(name))
    candidates = [
        (e.entity_id, e.text, lookup.entities[e.entity_id].name)
        for e in lookup.names
        if e.confirmed and not e.secret and codes & set(e.codes)
    ]
    return near_match_in(name, candidates)


class NearMatchTest(unittest.TestCase):
    """Names heard during play that sound like a known name (#394)."""

    def lookup(self, *extra: tuple[str, str, str, bool, str]) -> CampaignLookup:
        rows = (("a" * 32, "Hrothgar", "npc", False, CONFIRMED), *extra)
        entities = tuple(
            Entity(e, kind, n, "", status, None, "dm", 0) for e, n, kind, _, status in rows
        )
        aliases = tuple(
            Alias(e[:16] + "0" * 16, e, n, name_key(n), "full", None, secret, status, (), "dm", 0)
            for e, n, _, secret, status in rows
        )
        return CampaignLookup.build(LookupData(1, entities, aliases, (), ()))

    def test_a_misheard_known_name_comes_with_its_match(self) -> None:
        match = near_match(self.lookup(), "Rothgar")
        assert match is not None
        self.assertEqual((match.entity_id, match.name), ("a" * 32, "Hrothgar"))

    def test_one_word_needs_to_be_spelled_very_alike(self) -> None:
        names = self.lookup(("b" * 32, "Mara", "player_character", False, CONFIRMED))
        self.assertIsNone(near_match(names, "Marra"))  # 0.89: offered as new

    def test_two_words_need_a_little_less(self) -> None:
        names = self.lookup(("b" * 32, "Oskar Vane", "npc", False, CONFIRMED))
        match = near_match(names, "Oskar Vain")
        self.assertEqual(match.name if match else None, "Oskar Vane")

    def test_never_a_secret_or_a_suggested_name(self) -> None:
        secret = self.lookup(("b" * 32, "Belleros", "npc", True, CONFIRMED))
        self.assertIsNone(near_match(secret, "Bellerros"))
        suggested = self.lookup(("b" * 32, "Belleros", "npc", False, PROPOSED))
        self.assertIsNone(near_match(suggested, "Bellerros"))

    def test_two_known_names_about_as_close_means_none(self) -> None:
        names = self.lookup(
            ("b" * 32, "Marenne", "npc", False, CONFIRMED),
            ("c" * 32, "Marrene", "npc", False, CONFIRMED),
        )
        self.assertIsNone(near_match(names, "Marene"))

    def test_a_long_name_is_matched_quickly_and_right(self) -> None:
        names = self.lookup(("b" * 32, "Neverember", "npc", False, CONFIRMED))
        self.assertEqual(getattr(near_match(names, "Nevermber"), "name", None), "Neverember")


class GroupAlikeTest(unittest.TestCase):
    def test_a_short_name_inside_a_longer_one_is_one_question(self) -> None:
        (group,) = group_alike([Suggestion("Oskar Vane", 3), Suggestion("Vane", 2)])
        self.assertEqual((group.name, group.also, group.times), ("Oskar Vane", ("Vane",), 5))

    def test_two_spellings_that_sound_alike(self) -> None:
        (group,) = group_alike([Suggestion("Ulfgarr", 2), Suggestion("Ulfgar", 4)])
        self.assertEqual((group.name, group.also, group.times), ("Ulfgar", ("Ulfgarr",), 6))

    def test_one_word_names_need_to_be_very_alike_to_fold(self) -> None:
        names = {g.name for g in group_alike([Suggestion("Kael", 2), Suggestion("Kaela", 2)])}
        self.assertEqual(names, {"Kael", "Kaela"})  # 0.89: two different people, maybe

    def test_a_word_shared_by_two_names_stays_its_own_question(self) -> None:
        found = [
            Suggestion("Lord Neverember", 3),
            Suggestion("Lord Dagult", 3),
            Suggestion("Lord", 2),
        ]
        groups = {g.name: g.also for g in group_alike(found)}
        self.assertEqual(groups, {"Lord Neverember": (), "Lord Dagult": (), "Lord": ()})

    def test_all_found_before_the_cap(self) -> None:
        lines = [f"We saw Name{chr(65 + i)}x twice. Again Name{chr(65 + i)}x." for i in range(12)]
        self.assertEqual(len(find_new_names(lines, unlimited=True)), 12)
        self.assertEqual(len(find_new_names(lines)), MAX_SUGGESTIONS)

    def test_different_names_stay_apart(self) -> None:
        names = {
            g.name for g in group_alike([Suggestion("Oskar Vane", 3), Suggestion("Ulfgar", 2)])
        }
        self.assertEqual(names, {"Oskar Vane", "Ulfgar"})

    def test_another_name_of_a_known_entry_is_never_suggested(self) -> None:
        # "Frostmaiden" is another name of Auril: known_keys skips it already
        lines = ["We pray to the Frostmaiden.", "The Frostmaiden answers."]
        self.assertEqual(find_new_names(lines, ["auril", "frostmaiden"]), [])
