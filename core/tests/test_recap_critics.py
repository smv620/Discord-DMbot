"""The critics loop's pure parts (#2255): the motive check, PlotBot's review and the saved state."""

from __future__ import annotations

import json
import unittest

from dmbot.recaps import beats as bt
from dmbot.recaps import critics as cr
from dmbot.recaps import motives as mo
from dmbot.recaps.significance import Memory

LINES = {
    4: "We are going to Greywater Keep because the lantern keeper vanished there.",
    5: "Mira draws her blade and steps inside.",
    6: "Oskar wants to find the silver lock before the bells ring.",
}
SENTENCES = [
    "Mira drew her blade and stepped inside.",
    "She wanted to avenge her brother, who fell at the keep.",
    "Oskar hoped to find the silver lock before the bells rang.",
    "The party headed north to Greywater Keep because the lantern keeper vanished there.",
    "The hall was bare.",
]


class TheMotiveCheck(unittest.TestCase):
    def test_only_sentences_that_state_a_reason_or_plan_are_asked_about(self) -> None:
        asked = mo.candidates(SENTENCES)
        self.assertEqual([n for n, _ in asked], [2, 3, 4])
        self.assertEqual(mo.candidates(["The hall was bare.", "Mira drew her blade."]), [])

    def test_a_sentence_with_a_line_that_says_it_passes_and_an_invented_one_fails(self) -> None:
        asked = mo.candidates(SENTENCES)
        answer = [(2, [5], False), (3, [6], False), (4, [4], False)]
        bad = mo.failures(asked, answer, LINES, "")
        self.assertEqual([f.number for f in bad], [2])  # nobody said she wanted to avenge anyone

    def test_a_citation_has_to_be_a_real_line_that_shares_the_sentences_words(self) -> None:
        asked = mo.candidates(SENTENCES)
        # a line that does not exist, one that says something else, and a missing answer
        answer = [(2, [99], False), (3, [5], False)]
        self.assertEqual([f.number for f in mo.failures(asked, answer, LINES, "")], [2, 3, 4])

    def test_the_earlier_recap_counts_only_when_it_says_it(self) -> None:
        asked = [(2, SENTENCES[1])]
        earlier = "In the last session Mira swore to avenge her brother, who fell at the keep."
        self.assertEqual(mo.failures(asked, [(2, [], True)], LINES, earlier), [])
        self.assertEqual(len(mo.failures(asked, [(2, [], True)], LINES, "Nothing of the sort.")), 1)
        self.assertEqual(len(mo.failures(asked, [(2, [], False)], LINES, earlier)), 1)

    def test_an_answer_that_cannot_be_read_counts_as_nobody_said_it(self) -> None:
        self.assertIsNone(mo.parse("not json"))
        self.assertIsNone(mo.parse(json.dumps({"checks": [{"sentence": "2"}]})))
        asked = mo.candidates(SENTENCES)
        self.assertEqual(len(mo.failures(asked, [], LINES, "")), 3)

    def test_the_writer_is_told_the_sentence_and_to_say_only_what_was_done(self) -> None:
        (failure,) = mo.failures([(2, SENTENCES[1])], [], LINES, "")
        text = mo.instruction(failure)
        self.assertIn(SENTENCES[1], text)
        self.assertIn("say only what the characters did", text)


MEMORY = [
    Memory("m1", "The silver lock belongs to the Order of the Lantern.", True),
    Memory("m2", "The Order's grandmaster is secretly the baron.", False),
]
KNOWN = {"silver", "lock", "belongs", "order", "lantern", "keep", "party", "reaches", "greywater"}


def item(sentence: int, kind: str, change: str, *ties: str) -> dict[str, object]:
    return {"sentence": sentence, "kind": kind, "change": change, "ties": list(ties)}


def plot(items: list[dict[str, object]]) -> tuple[list[cr.Change], list[str]]:
    return cr.plot_changes(
        items,
        sentences=SENTENCES,
        memory=MEMORY,
        has_recap=True,
        known_words=KNOWN,
        allowed_names=["Mira", "Oskar", "Greywater Keep"],
        secret_names=["the baron"],
    )


class TheMotiveCheckOnALongSession(unittest.TestCase):
    def test_a_long_recap_is_asked_about_to_the_end(self) -> None:
        many = [f"Mira wanted to find the lantern number {n} before dawn." for n in range(65)]
        asked = mo.candidates(many)
        self.assertEqual(len(asked), 65)  # none is left unasked
        calls = mo.batches(asked)
        self.assertEqual([len(c) for c in calls], [30, 30, 5])
        self.assertEqual([n for c in calls for n, _ in c], [n for n, _ in asked])

    def test_a_reason_said_late_in_a_long_session_is_shown_and_can_be_cited(self) -> None:
        lines = {n: f"Chatter about dinner and weather, line {n}." for n in range(1, 1500)}
        lines[900] = "Mira wants to avenge her brother, he fell at the old keep."
        asked = [(2, "She wanted to avenge her brother, who fell at the keep.")]
        shown = mo.shown_lines(asked, lines)
        self.assertEqual(list(shown), [900])  # only what could back it, wherever it was said
        self.assertEqual(mo.failures(asked, [(2, [900], False)], lines, ""), [])
        # a line that was not shown cannot be cited, even if it would match
        self.assertEqual(len(mo.failures(asked, [(2, [901], False)], lines, "")), 1)
        self.assertIn("[900]", mo.message(asked, lines, ""))
        self.assertNotIn("[1000]", mo.message(asked, lines, ""))


class PlotBotsReview(unittest.TestCase):
    def test_a_secret_found_is_kept_even_with_an_odd_citation_and_a_long_list_of_small_ones(
        self,
    ) -> None:
        small = [item(1, "weight", "x") for _ in range(12)]
        got, _ = plot([*small, item(2, "secret", "it names the baron", "nonsense")])
        self.assertEqual(len(got), cr.MAX_CHANGES)
        self.assertTrue(got[0].hard)  # first: a long list never pushes it out
        self.assertEqual(got[0].kind, "secret")

    def test_its_words_reach_the_writer_only_when_the_players_already_know_them(self) -> None:
        got, _ = plot(
            [
                {
                    "sentence": 0,
                    "kind": "missed",
                    "change": "say the silver lock belongs to the order",
                    "ties": ["m1"],
                }
            ]
        )
        self.assertEqual(got[0].text, "the draft: say the silver lock belongs to the order")
        self.assertFalse(got[0].hard)

    def test_a_reason_resting_on_a_dm_only_item_or_a_secret_is_the_fixed_wording(self) -> None:
        got, _ = plot(
            [
                {
                    "sentence": 0,
                    "kind": "missed",
                    "change": "the order is run by the baron",
                    "ties": ["m2"],
                },
                item(0, "weight", "mention the baron", "m1"),
                {
                    "sentence": 3,
                    "kind": "continuity",
                    "change": "the keeper is the baron",
                    "ties": [],
                },
            ]
        )
        self.assertEqual(
            [c.text.split(": ", 1)[1] for c in got],
            [cr.GENERIC["missed"], cr.GENERIC["weight"], cr.GENERIC["continuity"]],
        )
        self.assertNotIn("baron", " ".join(c.text for c in got))

    def test_a_secret_is_hard_and_never_repeats_what_the_ai_said(self) -> None:
        got, _ = plot([item(2, "secret", "it names the baron", "m2")])
        (one,) = got
        self.assertTrue(one.hard)
        self.assertEqual(one.sentence, 2)
        self.assertNotIn("baron", one.text)
        self.assertIn(SENTENCES[1], one.text)

    def test_what_cites_something_it_was_not_given_or_is_malformed_is_cut(self) -> None:
        got, cut = plot(
            [
                item(0, "missed", "x", "m9"),  # unknown id
                item(99, "weight", "x"),  # no such sentence
                item(0, "contradiction", "x"),  # needs one
                item(1, "nonsense", "x"),
            ]
        )
        self.assertEqual(got, [])
        self.assertEqual(len(cut), 3)
        self.assertIsNone(cr.parse_plot("nope"))
        self.assertEqual(
            cr.parse_plot('{"changes": [1, {"kind": "missed"}]}'), [{"kind": "missed"}]
        )

    def test_at_most_a_few_changes_are_kept(self) -> None:
        items = [item(1, "weight", "x")] * 20
        self.assertEqual(len(plot(items)[0]), cr.MAX_CHANGES)

    def test_the_review_reads_the_beats_notes_the_memory_and_the_numbered_draft(self) -> None:
        beat = bt.Beat(1, "Mira found a lock", ("Mira",), (5,), "event", "", "It matters", False)
        text = cr.plot_message([beat], MEMORY, "Last time.", SENTENCES)
        for part in ("<beats>", "m1 [players know this]", "m2 [DM only]", "<previous>", "[2] She"):
            self.assertIn(part, text)
        self.assertIn("at most 8", cr.plot_system())


class TheSavedState(unittest.TestCase):
    def test_the_loop_survives_a_round_trip_and_unreadable_state_is_a_fresh_loop(self) -> None:
        changes = (cr.Change("motive", "t", True, 2), cr.Change("style", "s", False))
        best = cr.best_from("draft", ["m"], {"tension": 4}, changes)
        loop = cr.Loop(2, changes, best, False, ("note",), 1, (cr.Change("motive", "t", True, 3),))
        self.assertEqual(cr.loop_from_json(json.loads(json.dumps(loop.to_json()))), loop)
        junk: tuple[object, ...] = (None, "x", [], {"round": "2", "pending": "no"})
        for bad in junk:
            self.assertEqual(cr.loop_from_json(bad), cr.Loop())

    def test_the_best_draft_has_the_fewest_hard_failures_then_the_fewest_soft(self) -> None:
        one = cr.Change("motive", "t", True, 2)
        soft = cr.Change("style", "s", False)
        two_soft = cr.best_from("a", [], {}, [soft, soft])
        one_hard = cr.best_from("b", [], {}, [one])
        none = cr.best_from("c", [], {}, [])
        self.assertTrue(two_soft.better_than(one_hard))  # hard failures weigh first
        self.assertTrue(none.better_than(two_soft))
        self.assertFalse(two_soft.better_than(none))
        self.assertTrue(one_hard.better_than(None))
        self.assertEqual((one_hard.cuts, one_hard.unfixable), ((2,), False))
        self.assertTrue(cr.best_from("d", [], {}, [cr.Change("secret", "t", True)]).unfixable)

    def test_the_dm_reads_a_neutral_thing_to_check_not_an_order(self) -> None:
        got, _ = plot(
            [item(3, "continuity", "the keeper is the baron"), item(0, "weight", "x", "m1")]
        )
        self.assertEqual(
            [c.note for c in got],
            [f'Check: sentence 3 ("{SENTENCES[2]}") may not follow from where the story left off.',
             cr.NOTES["weight"]],
        )  # fmt: skip
        self.assertEqual(cr.leftover_notes(got), tuple(c.note for c in got))
        self.assertEqual(cr.style_changes(["keep the mood"])[0].note, "Check: style: keep the mood")
        self.assertNotIn("baron", " ".join(cr.leftover_notes(got)))

    def test_the_writer_hears_hard_changes_first_and_the_dm_keeps_only_soft_ones(self) -> None:
        changes = [cr.Change("style", "s", False), cr.Change("motive", "m", True, 1)]
        self.assertEqual(cr.writer_notes(changes), ["m", "s"])
        self.assertEqual(cr.leftover_notes(changes), ("s",))
        self.assertEqual(cr.count(changes), (1, 1))
