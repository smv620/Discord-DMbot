"""Things and places a session singles out with no name (#5173, session names slice 6): which are
offered, by code and never on the reader's word, on an invented session (`singled_cases.txt`).
Pure: no database, no Discord, no AI."""

from __future__ import annotations

import unittest
from pathlib import Path

from dmbot.memory import singled_out as so
from dmbot.memory.entity_words import in_sentence
from dmbot.memory.name_reader import KNOWN_NAME, SINGLED_OUT, Found, holds

CASES = Path(__file__).parent / "fixtures" / "story_graph" / "singled_cases.txt"


def texts() -> dict[str, str]:
    lines = [x for x in CASES.read_text().splitlines() if not x.startswith("#")]
    return {str(i): t for i, t in enumerate(lines, 1)}


def found(name: str, kind: str, said: dict[str, str]) -> Found:
    """The reader's finding, as `name_reader.tally(..., every=True)` gives it: every line holding
    the phrase."""
    lines = tuple(n for n, t in said.items() if holds(t, name))
    return Found(name, kind, SINGLED_OUT, len(lines), lines)


class WhatIsOffered(unittest.TestCase):
    def setUp(self) -> None:
        self.said = texts()

    def decide(self, *items: tuple[str, str], **kw: object) -> so.Decided:
        return so.decide(
            [found(n, k, self.said) for n, k in items],
            self.said,
            kw.get("known", {"garret", "hrothgar"}),  # type: ignore[arg-type]
            kw.get("players", {"cerric"}),  # type: ignore[arg-type]
        )

    def reasons(self, out: so.Decided) -> dict[str, str]:
        return {name: why for name, why, _t in out.refused}

    def test_a_place_the_story_comes_back_to_in_two_stretches_is_offered(self) -> None:
        out = self.decide(("gemstone mine", "place"))
        self.assertEqual([k.name for k in out.kept], ["Gemstone mine"])  # (one capital only)
        self.assertEqual(out.kept[0].heard, "gemstone mine")
        self.assertEqual(out.kept[0].times, 4)  # three by its words, one by "the mine"
        self.assertEqual(out.shorter_returns, 1)

    def test_a_door_said_three_times_in_one_scene_is_not(self) -> None:
        out = self.decide(("door", "item"))
        self.assertEqual(out.kept, [])
        self.assertEqual(self.reasons(out), {"door": so.ONE_STRETCH})

    def test_a_shorter_return_needs_a_head_word_no_other_phrase_shares(self) -> None:
        out = self.decide(("gemstone mine", "place"), ("silver mine", "place"))
        self.assertEqual(out.shorter_returns, 0)  # (two mines are never one)
        mine = next(k for k in out.kept if k.heard == "gemstone mine")
        self.assertEqual(mine.times, 3)

    def test_a_known_name_as_the_head_word_or_the_whole_phrase_is_refused(self) -> None:
        said = {
            **self.said,
            "200": "old hrothgar again",
            "300": "old hrothgar",
            "1": "old hrothgar",
        }
        out = so.decide([found("old hrothgar", "place", said)], said, {"hrothgar"}, set())
        self.assertEqual(self.reasons(out), {"old hrothgar": KNOWN_NAME})
        out = so.decide([found("gemstone mine", "place", self.said)], self.said, {"gemstone mine"})
        self.assertEqual(out.kept, [])

    def test_a_known_possessor_keeps_the_thing(self) -> None:
        out = self.decide(("Garret's old sled", "item"))
        self.assertEqual([k.name for k in out.kept], ["Garret's old sled"])

    def test_a_player_characters_own_gear_is_never_offered(self) -> None:
        out = self.decide(("Cerric's spellbook", "item"))
        self.assertEqual(self.reasons(out), {"Cerric's spellbook": so.EVERYDAY_ITEM})

    def test_an_item_that_is_not_one_particular_thing_is_refused(self) -> None:
        said = {"1": "a rope", "40": "the rope", "100": "that rope"}
        out = so.decide([found("rope", "item", said)], said, set(), is_item=lambda _n, _c: False)
        self.assertEqual(self.reasons(out), {"rope": so.EVERYDAY_ITEM})

    def test_heard_twice_is_traced_for_the_second_way_in(self) -> None:
        said = {"1": "the broken tower", "90": "the broken tower again"}
        out = so.decide([found("broken tower", "place", said)], said, set())
        self.assertEqual(self.reasons(out), {"broken tower": so.TWO_LINES_ONLY})

    def test_at_most_three_most_heard_first_and_the_rest_dropped(self) -> None:
        said = {**self.said, "1": "the broken tower", "65": "the broken tower", "131": "x"}
        said["129"] = "the broken tower at last"
        items = [
            found("gemstone mine", "place", said),
            found("azure tome", "item", said),
            found("Garret's old sled", "item", said),
            found("broken tower", "place", said),
        ]
        out = so.decide(items, said, {"garret"}, {"cerric"})
        self.assertEqual(len(out.kept), so.SINGLED_MAX)
        self.assertEqual(out.kept[0].heard, "gemstone mine")  # (heard most)
        self.assertEqual(sum(1 for _n, why, _t in out.refused if why == so.OVER_SINGLED), 1)

    def test_any_other_reason_is_left_alone(self) -> None:
        other = Found("Hrothgar", "person", "introduced", 1, ("3",))
        self.assertEqual(so.decide([other], self.said, set()).kept, [])
        self.assertEqual(so.decide([other], self.said, set()).refused, [])


class TheWords(unittest.TestCase):
    def test_head_word(self) -> None:
        self.assertEqual(so.head_word("gemstone mine"), "mine")
        self.assertEqual(so.head_word("Garret's old sled"), "sled")
        self.assertEqual(so.head_word("tome of ages"), "tome")

    def test_a_description_takes_a_capital_on_its_first_word_only(self) -> None:
        self.assertEqual(so.proposed("gemstone mine"), "Gemstone mine")

    def test_in_a_sentence_a_description_reads_as_the_story_said_it(self) -> None:
        self.assertEqual(in_sentence("Gemstone mine", True), "the gemstone mine")
        self.assertEqual(in_sentence("The old well", True), "the old well")
        self.assertEqual(in_sentence("Bryn Shander", False), "Bryn Shander")


if __name__ == "__main__":
    unittest.main()


class TheCardAndTheRecap(unittest.TestCase):
    def test_the_card_says_it_is_no_name_and_quotes_it_as_heard(self) -> None:
        from dmbot.ui import names as ui

        self.assertEqual(
            ui.singled_line("Gemstone mine", 4),
            "📌 Not a name, but the story keeps coming back to it: “gemstone mine” "
            "(heard 4 times).",
        )

    def test_the_three_answers_say_what_they_do(self) -> None:
        from dmbot.ui import names as ui

        self.assertEqual(
            (ui.KEEP_IT, ui.GIVE_IT_A_NAME, ui.NOT_WORTH_KEEPING),
            ("✅ Keep it", "✏️ Give it a name…", "🚫 Not worth keeping"),
        )

    def test_a_kept_one_says_so_on_its_name_card(self) -> None:
        from dmbot.ui import name_card

        self.assertEqual(
            name_card.DESCRIBED_LINE, "\n📌 Described in the story, no name of its own."
        )

    def test_the_recap_writer_is_given_it_as_the_story_says_it(self) -> None:
        from types import SimpleNamespace

        from dmbot.recaps.inputs import described_words

        names = SimpleNamespace(
            entities={
                "e1": SimpleNamespace(name="Gemstone mine", described=True),
                "e2": SimpleNamespace(name="Bryn Shander", described=False),
                "e3": SimpleNamespace(name="Azure tome", described=True),
            }
        )
        self.assertEqual(
            described_words(names, {"Gemstone mine", "Bryn Shander"}),
            {"Gemstone mine": "the gemstone mine"},  # (only what the recap may use)
        )
