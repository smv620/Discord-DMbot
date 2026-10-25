"""The "reader who was away" check (#2680): pure findings about a draft, in invented sentences.
Nothing here is a real recap or a real name."""

from __future__ import annotations

import unittest

from dmbot.recaps import away
from dmbot.recaps import draft as rd
from dmbot.recaps.critics import Change, count, leftover_notes, writer_notes

NAMES = ["Roan", "Maris", "Vell", "Greywater Keep", "Tavi"]
PCS = ["Maris", "Tavi"]
PREVIOUS = "Last time, Maris and Tavi reached the town of Vell and met the smith."


def find(*sentences: str, **kw: object) -> list[away.Finding]:
    args: dict[str, object] = {
        "names": NAMES, "aliases": {}, "previous": PREVIOUS, "present": PCS,
    }  # fmt: skip
    args.update(kw)
    return away.findings(list(sentences), **args)  # type: ignore[arg-type]


def kinds(found: list[away.Finding]) -> list[tuple[str, int, str]]:
    return [(f.kind, f.sentence, f.name) for f in found]


class Opening(unittest.TestCase):
    def test_a_pronoun_first_is_found_and_a_name_first_is_not(self) -> None:
        self.assertIn(("opening", 1, ""), kinds(find("He drew his blade. Maris and Tavi ran.")))
        self.assertIn(("opening", 1, ""), kinds(find("They had come a long way.")))
        self.assertNotIn(
            "opening", [k for k, _, _ in kinds(find("Maris drew her blade. Tavi ran."))]
        )

    def test_a_quote_mark_in_front_does_not_hide_it(self) -> None:
        self.assertEqual(away._first_word("“She waited.”"), "she")

    def test_a_cue_in_front_is_stripped_before_the_check_sees_it(self) -> None:
        sentences = rd.sentences_of("((pause)) She waited by the gate. Maris and Tavi ran.")
        self.assertIn(("opening", 1, ""), kinds(find(*sentences)))

    def test_a_time_or_a_link_word_is_not_a_pronoun(self) -> None:
        for sentence in ("That night Maris ran.", "Then Tavi followed.", "There was a storm."):
            with self.subTest(sentence):
                self.assertNotIn("opening", [k for k, _, _ in kinds(find(sentence))])


class Introductions(unittest.TestCase):
    def test_a_new_name_with_nothing_about_it_is_found(self) -> None:
        found = find("Maris met Roan at the keep.", "Tavi stayed at the gate.")
        self.assertIn(("introduce", 1, "Roan"), kinds(found))

    def test_an_appositive_or_a_title_before_counts_as_introducing(self) -> None:
        for sentence in (
            "Maris met Roan, a smith from the hills.",
            "Maris met the old smith Roan at the keep.",
            "Maris met a man called Roan.",
            "Maris met Roan (the smith) at the keep.",
        ):
            with self.subTest(sentence):
                self.assertNotIn("introduce", [k for k, _, _ in kinds(find(sentence, "Tavi ran."))])

    def test_a_word_between_an_article_and_the_name_does_not_make_an_introduction(self) -> None:
        for sentence in ("Maris gave a sword to Roan.", "The party met Roan at the keep."):
            with self.subTest(sentence):
                found = find(sentence, "Tavi ran.")
                self.assertIn(("introduce", 1, "Roan"), kinds(found))

    def test_a_name_the_earlier_recap_had_or_a_player_character_needs_none(self) -> None:
        found = find("Vell was quiet. Maris and Tavi walked on.")
        self.assertEqual([f for f in found if f.kind == "introduce"], [])

    def test_only_the_first_mention_is_looked_at(self) -> None:
        found = find("Maris met Roan, a smith.", "Roan gave a sword to Tavi.")
        self.assertEqual([f for f in found if f.kind == "introduce"], [])
        late = find("Maris met Roan.", "Roan, a smith, gave a sword to Tavi.")
        self.assertIn(("introduce", 1, "Roan"), kinds(late))  # introduced too late

    def test_another_spelling_counts_as_the_name(self) -> None:
        found = find("Maris met Rowan at the keep.", aliases={"Rowan": "Roan"})
        self.assertIn(("introduce", 1, "Roan"), kinds(found))

    def test_a_spelling_that_is_also_a_name_is_one_person(self) -> None:
        both = ["Roan", "Rowan"]
        found = find(
            "Maris met Rowan, a smith.", "Roan agreed.", names=both, aliases={"Rowan": "Roan"}
        )
        self.assertEqual([f for f in found if f.kind == "introduce"], [])
        said = find("Maris met Rowan.", "Roan agreed.", names=both, aliases={"Rowan": "Roan"})
        self.assertEqual(
            [f for f in said if f.kind == "introduce"], [away.Finding("introduce", 1, "Roan")]
        )
        earlier = find(
            "Maris met Rowan.", names=both, aliases={"Rowan": "Roan"}, previous="Roan sang."
        )
        self.assertEqual([f for f in earlier if f.kind == "introduce"], [])

    def test_a_name_not_in_the_draft_is_never_asked_about(self) -> None:
        self.assertEqual([f for f in find("Maris and Tavi rested.") if f.kind == "introduce"], [])


class Threads(unittest.TestCase):
    def test_a_favour_with_no_result_is_found_and_one_with_a_result_is_not(self) -> None:
        open_ = find("Maris owed Roan a favour.", "They rested at the keep.", "Tavi slept.")
        self.assertIn(("thread", 1, ""), kinds(open_))
        paid = find("Maris owed Roan a favour.", "At dawn she repaid it.", "Tavi slept.")
        self.assertNotIn("thread", [k for k, _, _ in kinds(paid)])

    def test_one_thread_a_draft(self) -> None:
        found = find("Roan warned Maris.", "He made a promise and a threat.", "Tavi ran.")
        self.assertEqual(len([f for f in found if f.kind == "thread"]), 1)

    def test_saying_it_is_still_open_counts(self) -> None:
        found = find(
            "Maris owed Roan a favour that was still unpaid.", "Tavi slept.", "Maris sighed."
        )
        self.assertNotIn("thread", [k for k, _, _ in kinds(found)])

    def test_the_last_sentence_is_the_closings_business_not_a_thread(self) -> None:
        found = find("Maris and Tavi rested.", "A warning came from the dark.")
        self.assertNotIn("thread", [k for k, _, _ in kinds(found)])


class Closing(unittest.TestCase):
    def test_a_closing_threat_with_nobody_in_it_is_found(self) -> None:
        found = find("Maris and Tavi rested.", "A threat gathered in the hills.")
        self.assertIn(("closing", 2, ""), kinds(found))

    def test_naming_the_party_or_a_player_character_is_enough(self) -> None:
        for last in ("A threat gathered against the party.", "A threat gathered against Tavi."):
            with self.subTest(last):
                found = find("Maris rested.", last)
                self.assertNotIn("closing", [k for k, _, _ in kinds(found)])


class Moments(unittest.TestCase):
    def test_a_player_character_in_the_session_but_not_the_recap_is_found(self) -> None:
        found = find("Maris led the way.", "Maris held the gate.")
        self.assertIn(("moment", 0, "Tavi"), kinds(found))

    def test_one_who_was_not_in_the_session_is_not_asked_about(self) -> None:
        found = find("Maris led the way.", present=["Maris"])
        self.assertEqual([f for f in found if f.kind == "moment"], [])


class WhatComesOut(unittest.TestCase):
    def test_a_clean_draft_has_no_findings_and_an_empty_one_none_either(self) -> None:
        clean = find(
            "Maris met Roan, a smith from the hills.",
            "Tavi stayed at the gate and watched the road.",
            "In the morning Maris paid the debt.",
        )
        self.assertEqual(clean, [])
        self.assertEqual(away.findings([], names=NAMES, aliases={}, previous="", present=PCS), [])

    def test_there_are_few_findings_and_they_are_soft_in_code_words(self) -> None:
        sentences = ["He saw Roan.", "Roan owed Maris a favour.", "A threat came."]
        found = away.findings(
            sentences, names=NAMES, aliases={}, previous="", present=["Maris", "Tavi"]
        )
        self.assertLessEqual(len(found), away.MAX_FINDINGS)
        got = away.changes(found, sentences)
        self.assertTrue(got and all(isinstance(c, Change) and not c.hard for c in got))
        self.assertEqual(count(got), (0, len(got)))
        for note in leftover_notes(got):
            self.assertTrue(note.startswith("Check: "))  # neutral, a thing to check, not an order

    def test_the_findings_never_ask_for_length(self) -> None:
        sentences = ["He saw Roan.", "Roan owed Maris a favour.", "Then a threat came."]
        found = find(*sentences, present=["Maris", "Tavi", "Vell"])
        self.assertEqual(
            {f.kind for f in found}, {"opening", "introduce", "thread", "closing", "moment"}
        )  # every kind is in this draft
        text = " ".join(writer_notes(away.changes(found, sentences))).casefold()
        for word in ("longer", "shorter", "paragraph", "sentences long", "more words", "fewer"):
            self.assertNotIn(word, text)

    def test_when_there_are_too_many_the_useful_ones_stay(self) -> None:
        names = [f"Name{i}" for i in range(8)]
        sentences = ["He ran."] + [f"Name{i} stood there." for i in range(8)]
        found = find(*sentences, names=names, present=["Maris"])
        kinds_ = [f.kind for f in found]
        self.assertLessEqual(len(found), away.MAX_FINDINGS)
        self.assertIn("opening", kinds_)
        self.assertIn("moment", kinds_)
        self.assertEqual(kinds_.count("introduce"), away.MAX_INTRODUCE)

    def test_is_note_knows_only_its_own_notes(self) -> None:
        sentences = ["He saw Roan.", "Roan owed Maris a favour.", "Then a threat came."]
        found = find(*sentences, present=["Maris", "Tavi", "Vell"])
        notes = away.notes(found, sentences)
        self.assertTrue(notes and all(away.is_note(n) for n in notes))
        self.assertFalse(away.is_note("Check: the style could not be checked this time."))

    def test_the_dm_notes_are_the_same_findings(self) -> None:
        sentences = ["He ran.", "Tavi followed."]
        found = find(*sentences)
        self.assertEqual(len(away.notes(found, sentences)), len(found))


if __name__ == "__main__":
    unittest.main()
