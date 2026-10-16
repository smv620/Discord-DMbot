"""The homebrew file (#1524): written and read back without loss, a block that is not an entry
reported and never guessed at, and the limits."""

from __future__ import annotations

import unittest

from dmbot.rules import homebrew_file as hf
from dmbot.rules.homebrew import HOMEBREW_MAX, Homebrew

NOW = 1_700_000_000


def entry(name: str, text: str, *, kind: str = "monster", **kw: object) -> Homebrew:
    return Homebrew(
        1,
        "c",
        kind,
        name,
        text,
        kw.get("summary"),
        bool(kw.get("known")),
        7,
        NOW,
        NOW,  # type: ignore[arg-type]
    )


class RoundTrip(unittest.TestCase):
    def test_write_then_parse_loses_nothing(self) -> None:
        entries = [
            entry(
                "Hollow King",
                "A skeleton.\nIt wears a crown.\n\nIt cannot be touched.",
                summary="Crowned.",
                known=True,
            ),
            entry("Moonblade", "A silver sword.", kind="item"),
            entry("Frost Lance", "Cold.", kind="spell", known=True),
        ]
        parsed = hf.parse(hf.write("Frostmaiden", entries))
        self.assertEqual(parsed.problems, ())
        got = {e.name: e for e in parsed.entries}
        for original in entries:
            back = got[original.name]
            self.assertEqual(
                (back.kind, back.text, back.summary, back.players_may_know),
                (original.kind, original.text, original.summary, original.players_may_know),
            )
        self.assertEqual(len(parsed.entries), 3)

    def test_words_that_look_like_the_file_s_own_marks_survive(self) -> None:
        tricky = "## not a heading\n\\## also not\nSummary: not a summary\nPlayers may know: yes"
        parsed = hf.parse(hf.write("X", [entry("Odd", tricky)]))
        self.assertEqual(parsed.problems, ())
        (back,) = parsed.entries
        self.assertEqual(back.text, tricky)
        self.assertIsNone(back.summary)
        self.assertFalse(back.players_may_know)

    def test_an_empty_campaign_writes_a_file_that_reads_as_nothing(self) -> None:
        parsed = hf.parse(hf.write("Empty", []))
        self.assertEqual((parsed.entries, parsed.problems), ((), ()))


class Reading(unittest.TestCase):
    def test_a_hand_written_file(self) -> None:
        text = (
            "My homebrew\n# a note\n\n## The Hollow King (Monster)\nSummary: Crowned.\n"
            "Players may know: yes\n\nA skeleton.\n\n## Moonblade (item)\n\nA sword.\n"
        )
        parsed = hf.parse(text)
        self.assertEqual(parsed.problems, ())
        king, blade = parsed.entries
        self.assertEqual(
            (king.name, king.kind, king.summary, king.players_may_know),
            ("The Hollow King", "monster", "Crowned.", True),
        )
        self.assertEqual((blade.text, blade.players_may_know), ("A sword.", None))  # not stated

    def test_blocks_that_are_not_entries_are_reported_with_the_line(self) -> None:
        text = (
            "Title\nstray words before any entry\n"
            "## No kind\nWords.\n"
            "## Bad kind (dragon)\nWords.\n"
            "## (item)\nWords.\n"
            "## Empty (item)\n\n"
            "## Fine (rule)\n\nGood words.\n"
            "## Fine (item)\n\nSame name again.\n"
            "## Odd (item)\nPlayers may know: maybe\n\nWords.\n"
        )
        parsed = hf.parse(text)
        self.assertEqual([e.name for e in parsed.entries], ["Fine"])
        whys = {(p.line, p.why) for p in parsed.problems}
        self.assertIn((2, hf.NOT_AN_ENTRY), whys)
        self.assertIn((3, hf.NO_KIND), whys)
        self.assertIn((5, hf.BAD_KIND), whys)
        self.assertIn((7, hf.NO_NAME), whys)
        self.assertIn((9, hf.NO_TEXT), whys)
        self.assertIn((14, hf.TWICE), whys)
        self.assertIn((17, hf.BAD_PLAYERS), whys)

    def test_too_long_parts_are_refused_not_cut(self) -> None:
        big = "## Long (item)\n\n" + "x" * 2001
        self.assertEqual(hf.parse(big).entries, ())
        self.assertEqual(hf.parse(big).problems[0].why, hf.TEXT_TOO_LONG)
        self.assertEqual(
            hf.parse(f"## {'n' * 81} (item)\n\nWords.").problems[0].why, hf.NAME_TOO_LONG
        )
        self.assertEqual(
            hf.parse(f"## A (item)\nSummary: {'s' * 161}\n\nWords.").problems[0].why,
            hf.SUMMARY_TOO_LONG,
        )

    def test_more_than_the_limit_leaves_the_rest_out(self) -> None:
        text = "".join(f"## E{n} (rule)\n\nWords {n}.\n" for n in range(HOMEBREW_MAX + 3))
        parsed = hf.parse(text)
        self.assertEqual(len(parsed.entries), HOMEBREW_MAX)
        self.assertEqual(parsed.problems[0].why, hf.TOO_MANY)

    def test_a_huge_file_is_not_read_at_all(self) -> None:
        parsed = hf.parse("x" * (hf.MAX_FILE_CHARS + 1))
        self.assertEqual((parsed.entries, parsed.problems[0].why), ((), hf.TOO_BIG))

    def test_only_twenty_problems_are_listed_then_a_count(self) -> None:
        parsed = hf.parse("".join(f"## B{n} (dragon)\n\nWords.\n" for n in range(30)))
        self.assertEqual(len(parsed.problems), hf.MAX_PROBLEMS + 1)
        self.assertIn("10 more", parsed.problems[-1].why)

    def test_the_filename_is_plain(self) -> None:
        self.assertEqual(hf.filename("Frostmaiden: Rime!"), "homebrew-frostmaiden-rime.txt")
        self.assertEqual(hf.filename("???"), "homebrew-campaign.txt")


if __name__ == "__main__":
    unittest.main()


class TheTemplate(unittest.TestCase):
    def test_an_empty_campaign_downloads_a_file_with_an_example_that_is_not_an_entry(self) -> None:
        text = hf.write("Empty", [])
        self.assertIn("# ## Hollow King (monster)", text)  # commented out: nothing to load
        parsed = hf.parse(text)
        self.assertEqual((parsed.entries, parsed.problems), ((), ()))
