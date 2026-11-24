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
        self.assertEqual(out.shorter_returns, [("gemstone mine", 1)])  # (its own count)
        self.assertEqual(out.kept[0].said, "gemstone mine")

    def test_a_door_said_three_times_in_one_scene_is_not(self) -> None:
        out = self.decide(("door", "item"))
        self.assertEqual(out.kept, [])
        self.assertEqual(self.reasons(out), {"door": so.ONE_STRETCH})

    def test_a_shorter_return_needs_a_head_word_no_other_phrase_shares(self) -> None:
        out = self.decide(("gemstone mine", "place"), ("silver mine", "place"))
        self.assertEqual(out.shorter_returns, [])  # (two mines are never one)
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
        self.assertEqual(in_sentence("Gemstone mine", "gemstone mine"), "the gemstone mine")
        self.assertEqual(in_sentence("The old well", "The old well"), "the old well")
        self.assertEqual(in_sentence("Bryn Shander", None), "Bryn Shander")


class TheCardAndTheRecap(unittest.TestCase):
    def test_the_card_says_it_is_no_name_and_quotes_it_as_heard(self) -> None:
        from dmbot.ui import names as ui

        self.assertEqual(
            ui.singled_line("gemstone mine", 4),
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
                "e1": SimpleNamespace(name="Gemstone mine", described="gemstone mine"),
                "e2": SimpleNamespace(name="Bryn Shander", described=None),
                "e3": SimpleNamespace(name="Azure tome", described="azure tome"),
            }
        )
        self.assertEqual(
            described_words(names, {"Gemstone mine", "Bryn Shander"}),
            {"Gemstone mine": "the gemstone mine"},  # (only what the recap may use)
        )


class TheReviewersCases(unittest.TestCase):
    """The reviewer's and the ux-critic's points on slice 6a (#5173)."""

    def test_a_possessive_reads_as_the_story_said_it(self) -> None:
        from dmbot.ui import names as ui

        sled = "Garret's old sled"
        self.assertEqual(in_sentence(sled, sled), "Garret's old sled")
        self.assertIn("\u201cGarret's old sled\u201d", ui.singled_line(sled, 3))
        self.assertEqual(in_sentence("Gemstone mine", "gemstone mine"), "the gemstone mine")
        self.assertEqual(in_sentence("The azure tome", "The azure tome"), "the azure tome")

    def test_one_capital_unless_the_lines_give_more(self) -> None:
        self.assertEqual(
            so.proposed("gemstone Mine", ["the gemstone mine is shut"]), "Gemstone mine"
        )
        self.assertEqual(
            so.proposed("old bryn shander gate", ["the old Bryn Shander gate"]),
            "Old Bryn Shander gate",
        )

    def test_a_player_characters_gear_by_first_name_or_nickname(self) -> None:
        players = so.player_keys(["Cerric Ambere", "Ceri"])
        self.assertTrue({"cerric ambere", "cerric", "ceri"} <= players)
        said = {"1": "cerric's spellbook", "70": "cerric's spellbook", "130": "cerric's spellbook"}
        out = so.decide([found("Cerric's spellbook", "item", said)], said, set(), players)
        self.assertEqual(out.refused[0][1], so.EVERYDAY_ITEM)

    def test_only_a_live_name_refuses_by_the_head_word(self) -> None:
        said = {"1": "the old mine", "70": "the old mine", "130": "the old mine"}
        item = found("old mine", "place", said)
        # ("mine" is a word the DM refused as a name once: no reason to refuse the phrase)
        out = so.decide([item], said, {"mine"}, live={"bryn shander"})
        self.assertEqual([k.heard for k in out.kept], ["old mine"])
        out = so.decide([item], said, set(), live={"mine"})
        self.assertEqual(out.kept, [])

    def test_lines_not_readable_now_are_not_counted(self) -> None:
        said = texts()
        item = found("gemstone mine", "place", said)
        readable = {n: t for n, t in said.items() if n != "118"}  # (its speaker stopped)
        out = so.decide([item], readable, set())
        self.assertNotIn("118", out.kept[0].lines)

    def test_the_replies_say_what_happened_in_the_storys_words(self) -> None:
        from dmbot.ui import names as ui

        self.assertEqual(
            ui.not_kept_note("Gemstone mine", "gemstone mine"),
            "🚫 Not kept. DMbot won't ask about **the gemstone mine** again.",
        )
        self.assertEqual(
            ui.kept_note(in_sentence("Gemstone mine", "gemstone mine"), "place", ()),
            "✅ Kept **the gemstone mine** as a place.",
        )
        self.assertIn("Type its name, or press ✅ Keep it", ui.SAME_AS_DESCRIBED)

    def test_the_recap_writer_sees_only_the_storys_words(self) -> None:
        from dmbot.recaps.draft import _with_familiar

        familiar = {"Gemstone mine": "the gemstone mine", "Garret Voss": "Garret"}
        self.assertEqual(_with_familiar("Gemstone mine", familiar), "the gemstone mine")
        self.assertEqual(
            _with_familiar("Garret Voss", familiar), "[familiar: Garret; full: Garret Voss]"
        )

    def test_the_card_is_titled_and_quoted_in_the_storys_words(self) -> None:
        from dmbot.memory.models import Entity
        from dmbot.ui import names as ui

        mine = Entity(
            "e1", "place", "Gemstone mine", "", "proposed", None, "scan", 1,
            described="gemstone mine",
        )  # fmt: skip
        text = ui.suggestion_text(mine, 1, counts=(4, 0), singled=True)
        self.assertIn("📝 **the gemstone mine**", text)
        self.assertIn("\u201cgemstone mine\u201d (heard 4 times)", text)
        self.assertNotIn("a new name?", text)


class TheExpertsCases(unittest.TestCase):
    """The Expert's points on slice 6a (#5173): short returns need the whole phrase twice, a kept
    description's head word is a known name, and the mark holds the story's own words."""

    def test_one_cited_line_never_makes_every_the_gate_its_own(self) -> None:
        said = {"1": "the north gate creaks", "40": "the gate", "80": "the gate", "120": "the gate"}
        out = so.decide([found("north gate", "place", said)], said, set())
        self.assertEqual(out.kept, [])
        self.assertEqual(out.shorter_returns, [])
        self.assertEqual(self.reasons(out), {"north gate": so.TOO_FEW})

    def test_two_cited_lines_then_the_returns_are_counted_for_that_phrase(self) -> None:
        said = {"1": "the north gate", "70": "past the north gate", "100": "the gate", "130": "x"}
        said["130"] = "that gate again"
        out = so.decide([found("north gate", "place", said)], said, set())
        self.assertEqual([k.heard for k in out.kept], ["north gate"])
        self.assertEqual(out.kept[0].times, 4)
        self.assertEqual(out.shorter_returns, [("north gate", 2)])

    def test_the_words_are_the_cited_lines_own(self) -> None:
        said = {
            "1": "We wait for the Easthaven ferry.",
            "70": "The Easthaven ferry is late.",
            "130": "Still no Easthaven ferry.",
        }
        (ferry,) = so.decide([found("easthaven ferry", "place", said)], said, set()).kept
        self.assertEqual(ferry.said, "Easthaven ferry")
        self.assertEqual(in_sentence(ferry.name, ferry.said), "the Easthaven ferry")
        mine = so.decide([found("gemstone mine", "place", texts())], texts(), set()).kept[0]
        self.assertEqual(in_sentence(mine.name, mine.said), "the gemstone mine")
        sled = {str(n): "Garret's old sled is here." for n in (1, 70, 130)}
        (kept,) = so.decide([found("Garret's old sled", "item", sled)], sled, set()).kept
        self.assertEqual(in_sentence(kept.name, kept.said), "Garret's old sled")

    def test_a_kept_descriptions_head_word_is_a_known_name_when_no_other_shares_it(self) -> None:
        self.assertEqual(
            so.described_heads(["gemstone mine", "Easthaven ferry"]), {"mine", "ferry"}
        )
        self.assertEqual(so.described_heads(["north gate", "south gate"]), set())  # (which one?)
        said = {"1": "the old mine", "70": "the old mine", "130": "the old mine"}
        heads = so.described_heads(["gemstone mine"])
        out = so.decide([found("old mine", "place", said)], said, heads, live=heads)
        self.assertEqual(self.reasons(out), {"old mine": KNOWN_NAME})

    def reasons(self, out: so.Decided) -> dict[str, str]:
        return {name: why for name, why, _t in out.refused}


if __name__ == "__main__":
    unittest.main()
