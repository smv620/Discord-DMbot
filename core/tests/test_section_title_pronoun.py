"""A section title's "his" (#2790 slice 3, the owner's case 1): a backstory section titled
"Tor Vane (Elf Hunter)" says "His elder brother, Kell, vanished ... His younger sister, Mira, ...".
The reader keeps the AI's "Tor Vane is Kell's brother" with the title in the quote, and leaves
every other "his" as it was. Invented names; no AI, network or database."""

from __future__ import annotations

import json
import time
import unittest
from collections import Counter
from collections.abc import Collection

from dmbot.memory import background as bg
from dmbot.memory.models import name_key


def read(
    text: str, *lines: dict[str, str], others: Collection[str] | None = None
) -> tuple[list[bg.Connection], list[bg.Unsettled], Counter[str]]:
    lost: Counter[str] = Counter()
    held: list[bg.Unsettled] = []
    reply = json.dumps({"connections": list(lines)})
    got = bg.read_connections(reply, text, dropped=lost, unsettled=held, others=others)
    return got, held, lost


# (as a lore read gives them: the campaign's and this read's names, `bg.reach_names`)
NAMES = bg.reach_names(name_key(n) for n in ("Tor Vane", "Kell", "Mira Dask", "Mira", "Brannoc"))


def kin(b: str, quote: str) -> dict[str, str]:
    return {"a": "Tor Vane", "how": "family", "b": b, "quote": quote}


SECTION = (
    "Tor Vane (Elf Hunter)\n"
    "His elder brother, Kell, vanished in the marsh. His younger sister, Mira, still tends the "
    "family forge."
)
KELL = "His elder brother, Kell, vanished in the marsh"
MIRA = "His younger sister, Mira, still tends the family forge"


class TheTitle(unittest.TestCase):
    def test_both_relatives_are_kept_with_the_title_in_the_quote(self) -> None:
        got, held, lost = read(SECTION, kin("Kell", KELL), kin("Mira", MIRA))
        self.assertEqual([c.b for c in got], ["Kell", "Mira"])
        self.assertEqual(held, [])
        self.assertTrue(all(c.quote.startswith("Tor Vane") for c in got))
        self.assertIn(MIRA, got[1].quote)
        self.assertEqual(lost["quote_heading"], 1)  # (Kell's sentence borrows the line before)

    def test_a_title_too_short_to_borrow_from_counts(self) -> None:
        # (a line of three words or more was already borrowed as the sentence before, #2757)
        got, _, lost = read(f"Tor Vane\n{KELL}.", kin("Kell", KELL))
        self.assertEqual([c.quote for c in got], [f"Tor Vane … {KELL}"])
        self.assertEqual(lost["quote_heading"], 1)

    def test_a_markdown_title_counts_without_its_marks(self) -> None:
        text = f"## Tor Vane\n\nA quiet tracker.\n\n{KELL}."
        got, _, lost = read(text, kin("Kell", KELL))
        self.assertEqual([c.quote for c in got], [f"Tor Vane … {KELL}"])
        self.assertEqual(lost["quote_heading"], 1)

    def test_a_paragraph_between_the_title_and_the_line_is_still_the_section(self) -> None:
        text = f"Tor Vane (Elf Hunter)\n\nA quiet tracker from the north.\n\n{KELL}."
        got, _, lost = read(text, kin("Kell", KELL))
        self.assertEqual([c.quote for c in got], [f"Tor Vane (Elf Hunter) … {KELL}"])
        self.assertEqual(lost["quote_heading"], 1)

    def test_with_the_reads_names_a_relative_across_a_full_stop_is_left_to_the_check(self) -> None:
        # Kell's line keeps the title. Mira's "His" comes after "Kell" across a full stop, where
        # the relative can be the new "his" ("His brother, Daric, was a smith. His wife, Ana",
        # #3238): it waits for the who-is-it check, never kept on the title's word alone.
        got, held, _ = read(SECTION, kin("Kell", KELL), kin("Mira", MIRA), others=NAMES)
        self.assertEqual([c.b for c in got], ["Kell"])
        self.assertEqual([u.made.b for u in held], ["Mira"])

    def test_windows_line_breaks_work(self) -> None:
        got, _, _ = read(SECTION.replace("\n", "\r\n"), kin("Kell", KELL), kin("Mira", MIRA))
        self.assertEqual(len(got), 2)


class NotATitle(unittest.TestCase):
    def test_a_title_that_does_not_name_the_end_lends_nothing(self) -> None:
        got, _, lost = read(f"Early Years\n{KELL}.", kin("Kell", KELL))
        self.assertEqual(lost["quote_heading"], 0)
        self.assertEqual(got, [])

    def test_a_line_of_the_story_is_no_title(self) -> None:
        text = f"Tor Vane left at dawn.\n\nRain fell all week.\n\n{KELL}."
        got, _, lost = read(text, kin("Kell", KELL))
        self.assertEqual(lost["quote_heading"], 0)
        self.assertEqual(got, [])

    def test_a_pronoun_mid_sentence_is_not_the_titles(self) -> None:
        quote = "The guard said that his brother, Kell, vanished in the marsh"
        got, held, lost = read(f"Tor Vane\n{quote}.", kin("Kell", quote))
        self.assertEqual((got, lost["quote_heading"], len(held)), ([], 0, 1))

    def test_a_he_or_she_is_not_the_titles(self) -> None:
        quote = "He is the brother of Kell, who vanished in the marsh"
        got, _, lost = read(f"Tor Vane\nRain fell all week.\n\n{quote}.", kin("Kell", quote))
        self.assertEqual((got, lost["quote_heading"]), ([], 0))

    def test_a_his_that_opens_a_later_sentence_of_the_quote_is_not_the_titles(self) -> None:
        quote = "Kell came home. His sister, Mira, stayed"
        got, _, lost = read(f"Tor Vane\n{quote}.", kin("Mira", quote))
        self.assertEqual(lost["quote_heading"], 0)
        self.assertEqual(got, [])

    def test_another_name_between_the_title_and_the_quote_leaves_it_to_the_check(self) -> None:
        for text, quote in (
            (
                "Tor Vane (Elf Hunter)\nMira Dask joined the guard. Her elder brother, Kell, "
                "vanished in the marsh.",
                "Her elder brother, Kell, vanished in the marsh",
            ),
            (f"Tor Vane\n\nBrannoc raised the boy.\n\n{KELL}.", KELL),
        ):
            with self.subTest(text=text[:30]):
                got, held, lost = read(text, kin("Kell", quote), others=NAMES)
                self.assertEqual((got, lost["quote_heading"], len(held)), ([], 0, 1))

    def test_a_table_row_or_a_spoken_line_is_no_title(self) -> None:
        for line in ("Tor Vane (the rogue) | NPC", '"Run, Tor Vane!"', "(Tor Vane left.)"):
            with self.subTest(line=line):
                got, _, lost = read(f"{line}\nRain fell.\n\n{KELL}.", kin("Kell", KELL))
                self.assertEqual((got, lost["quote_heading"]), ([], 0))

    def test_a_window_too_long_for_the_stored_quote_is_not_made(self) -> None:
        # (the stored quote keeps `REACH_CHARS`: a longer window would lose a name at the end)
        long = "His elder brother, Kell, " + "who " * 120 + "vanished"
        lost: Counter[str] = Counter()
        got = bg._widen(f"Tor Vane\n{long}.", long.split(), "Tor Vane", "Kell", lost=lost)
        self.assertEqual((got, lost["quote_heading"]), (None, 0))

    def test_a_long_document_is_not_read_back_to_its_start(self) -> None:
        story = "\n".join(["The long road ran on through the hills and the rain."] * 5000)
        started = time.monotonic()
        read(f"{story}\n\n{KELL}.", kin("Kell", KELL))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_deep_in_a_long_section_the_title_lends_nothing(self) -> None:
        filler = " ".join(["The road was long and the rain did not stop."] * 15)
        _, _, lost = read(f"Tor Vane\n{filler}\n\n{KELL}.", kin("Kell", KELL))
        self.assertEqual(lost["quote_heading"], 0)


class TheUnnamedPath(unittest.TestCase):
    def test_a_section_titles_her_mother_is_the_titles_character_s(self) -> None:
        # (the unnamed-people reader widens with no pronoun needed, #2959: the title lends too)
        text = "Tor Vane\nHer mother was a weaver in the north."
        words = ["Her", "mother", "was", "a", "weaver", "in", "the", "north"]
        got = bg._widen(text, words, "Tor Vane", "mother", needs_pronoun=False)
        self.assertEqual(got, ["Tor", "Vane", "…", *words])


if __name__ == "__main__":
    unittest.main()
