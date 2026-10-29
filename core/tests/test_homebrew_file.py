"""The homebrew file (#1524): written and read back without loss, a block that is not an entry
reported and never guessed at, and the limits."""

from __future__ import annotations

import dataclasses
import unittest

from dmbot.rules import homebrew_file as hf
from dmbot.rules.homebrew import HOMEBREW_MAX, Homebrew

NOW = 1_700_000_000


def entry(
    name: str, text: str, *, kind: str = "monster", summary: str | None = None, on: bool = True
) -> Homebrew:
    return Homebrew(1, "c", kind, name, text, summary, 7, NOW, NOW, enabled=on)


class RoundTrip(unittest.TestCase):
    def test_write_then_parse_loses_nothing(self) -> None:
        entries = [
            entry(
                "Hollow King",
                "A skeleton.\nIt wears a crown.\n\nIt cannot be touched.",
                summary="Crowned.",
            ),
            entry("Moonblade", "A silver sword.", kind="item", on=False),
            entry("Frost Lance", "Cold.", kind="spell"),
        ]
        parsed = hf.parse(hf.write("Frostmaiden", entries, "2024"))
        self.assertEqual(parsed.problems, ())
        got = {e.name: e for e in parsed.entries}
        for original in entries:
            back = got[original.name]
            self.assertEqual(
                (back.kind, back.text, back.summary, back.enabled),
                (
                    original.kind,
                    original.text,
                    original.summary,
                    None if original.enabled else False,
                ),
            )
        self.assertEqual(len(parsed.entries), 3)

    def test_species_subclass_and_background_write_and_read_back(self) -> None:
        entries = [
            entry("Moonfolk", "Pale and tall.", kind="species"),
            entry("Oath of Dusk", "A paladin path.", kind="subclass"),
            entry("Lamplighter", "You kept the lamps.", kind="background"),
        ]
        text = hf.write("X", entries, "2024")
        self.assertIn("## Moonfolk (species)", text)
        parsed = hf.parse(text)
        self.assertEqual(parsed.problems, ())
        self.assertEqual(
            sorted((e.name, e.kind) for e in parsed.entries),
            [("Lamplighter", "background"), ("Moonfolk", "species"), ("Oath of Dusk", "subclass")],
        )

    def test_words_that_look_like_the_file_s_own_marks_survive(self) -> None:
        tricky = "## not a heading\n\\## also not\nSummary: not a summary\nPlayers may know: yes"
        parsed = hf.parse(hf.write("X", [entry("Odd", tricky)], "2024"))
        self.assertEqual(parsed.problems, ())
        (back,) = parsed.entries
        self.assertEqual(back.text, tricky)
        self.assertIsNone(back.summary)
        self.assertIsNone(back.enabled)

    def test_an_empty_campaign_writes_a_file_that_reads_as_nothing(self) -> None:
        parsed = hf.parse(hf.write("Empty", [], "2024"))
        self.assertEqual((parsed.entries, parsed.problems), ((), ()))


def book(name: str, edition: str | None, *, summary: str | None = None) -> Homebrew:
    return dataclasses.replace(
        entry(name, "From the book.", kind="spell", summary=summary),
        from_book=True,
        book_edition=edition,
    )


class SharedBookTags(unittest.TestCase):
    """An entry from a shared rulebook carries the players' card tags under its heading (#1584)."""

    def lines(self, e: Homebrew, target: str = "2024") -> list[str]:
        out = hf.write("Frostmaiden", [e], target).split("\n")
        return out[out.index(f"## {e.name} ({e.kind})") :]

    def test_the_campaigns_own_entry_has_no_tag(self) -> None:
        lines = self.lines(entry("Hollow King", "Mine."))
        self.assertNotIn("[Shared book]", "\n".join(lines))

    def test_a_2024_book_entry_is_tagged_shared_book(self) -> None:
        lines = self.lines(book("Fireball", "2024"))
        self.assertEqual(lines[1], "[Shared book]")

    def test_a_2014_book_in_a_2024_campaign_is_tagged_legacy_too(self) -> None:
        lines = self.lines(book("Fireball", "2014"))
        self.assertEqual(lines[1], "[Shared book] [Legacy 2014]")

    def test_a_2014_book_in_a_2014_campaign_is_not_legacy(self) -> None:
        lines = self.lines(book("Fireball", "2014"), target="2014")
        self.assertEqual(lines[1], "[Shared book]")

    def test_an_add_on_book_or_an_older_entry_is_shared_book_without_legacy(self) -> None:
        for edition in ("other", None):
            self.assertEqual(self.lines(book("Fireball", edition))[1], "[Shared book]")

    def test_the_tag_comes_before_the_summary_and_the_off_line(self) -> None:
        off = dataclasses.replace(
            book("Fireball", "2014", summary="A ball of fire."), enabled=False
        )
        lines = self.lines(off)
        self.assertEqual(
            lines[1:4],
            ["[Shared book] [Legacy 2014]", "Summary: A ball of fire.", "Off: yes"],
        )

    def test_a_file_with_the_tags_reads_back_without_them_and_never_from_a_book(self) -> None:
        entries = [
            book("Fireball", "2014", summary="A ball of fire."),
            book("Moonlance", "2024"),
            entry("Hollow King", "Mine."),
        ]
        parsed = hf.parse(hf.write("Frostmaiden", entries, "2024"))
        self.assertEqual(parsed.problems, ())
        got = {e.name: e for e in parsed.entries}
        self.assertEqual(set(got), {"Fireball", "Moonlance", "Hollow King"})
        self.assertEqual(got["Fireball"].text, "From the book.")  # the tag is not in the words
        self.assertEqual(got["Fireball"].summary, "A ball of fire.")
        self.assertFalse(hasattr(got["Fireball"], "from_book"))  # a file never says it

    def test_a_tag_with_no_blank_line_after_it_is_skipped_and_the_words_follow(self) -> None:
        (back,) = hf.parse("## X (spell)\n[Shared book]\nwords").entries
        self.assertEqual(back.text, "words")

    def test_another_bracketed_line_under_the_heading_stays_as_words(self) -> None:
        (back,) = hf.parse("## X (spell)\n[Something else]\nwords").entries
        self.assertEqual(back.text, "[Something else]\nwords")

    def test_the_tag_reads_with_windows_line_ends_and_the_short_form_alone(self) -> None:
        parsed = hf.parse("## X (spell)\r\n[Shared book]\r\nOff: yes\r\n\r\nwords")
        (back,) = parsed.entries
        self.assertEqual((back.text, back.enabled), ("words", False))

    def test_the_help_text_explains_the_note(self) -> None:
        self.assertIn("[Shared book]", hf.HELP)

    def test_only_the_exact_tag_lines_directly_under_the_heading_are_skipped(self) -> None:
        text = "## A (spell)\n\n[Shared book]\n"  # words after a blank line stay the words
        (back,) = hf.parse(text).entries
        self.assertEqual(back.text, "[Shared book]")
        later = "## B (spell)\nSummary: s\n[Shared book]\n\nwords"
        parsed = hf.parse(later)
        self.assertEqual(parsed.entries[0].text, "[Shared book]\n\nwords")  # not under the heading


class Reading(unittest.TestCase):
    def test_a_hand_written_file(self) -> None:
        text = (
            "My homebrew\n# a note\n\n## The Hollow King (Monster)\nSummary: Crowned.\n"
            "Off: yes\n\nA skeleton.\n\n## Moonblade (item)\n\nA sword.\n"
        )
        parsed = hf.parse(text)
        self.assertEqual(parsed.problems, ())
        king, blade = parsed.entries
        self.assertEqual(
            (king.name, king.kind, king.summary, king.enabled),
            ("The Hollow King", "monster", "Crowned.", False),
        )
        self.assertEqual((blade.text, blade.enabled), ("A sword.", None))  # not stated

    def test_off_no_and_the_older_players_line(self) -> None:
        text = (
            "## A (item)\nOff: no\n\nWords.\n"
            "## B (item)\nPlayers may know: yes\nSummary: Short.\n\nWords.\n"
            "## C (item)\nPlayers may know: no\n\nWords.\n"
        )
        parsed = hf.parse(text)
        self.assertEqual(parsed.problems, ())
        a, b, c = parsed.entries
        # "Players may know" came before on/off and is accepted and ignored (part 2).
        self.assertEqual((a.enabled, b.enabled, c.enabled, b.summary), (True, None, None, "Short."))

    def test_only_an_off_entry_is_marked_in_the_written_file(self) -> None:
        text = hf.write(
            "T", [entry("Lamp", "Glows.", kind="item", on=False), entry("Rope", "x")], "2024"
        )
        self.assertEqual(text.split("\n").count("Off: yes"), 1)
        self.assertNotIn("Players may know", text)
        self.assertIn("## Lamp (item)\nOff: yes\n", text)

    def test_blocks_that_are_not_entries_are_reported_with_the_line(self) -> None:
        text = (
            "Title\nstray words before any entry\n"
            "## No kind\nWords.\n"
            "## Bad kind (dragon)\nWords.\n"
            "## (item)\nWords.\n"
            "## Empty (item)\n\n"
            "## Fine (feat)\n\nGood words.\n"
            "## Fine (item)\n\nSame name again.\n"
            "## Odd (item)\nOff: maybe\n\nWords.\n"
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
        self.assertIn((17, hf.BAD_OFF), whys)

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
        text = "".join(f"## E{n} (feat)\n\nWords {n}.\n" for n in range(HOMEBREW_MAX + 3))
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
        text = hf.write("Empty", [], "2024")
        self.assertIn("# ## Hollow King (monster)", text)  # commented out: nothing to load
        parsed = hf.parse(text)
        self.assertEqual((parsed.entries, parsed.problems), ((), ()))


class Untrusted(unittest.TestCase):
    def test_a_heading_of_spaces_does_not_stall_the_parser(self) -> None:
        import time

        for line in (
            "## a" + " " * 3999 + "(",
            "## a" + " " * 2000 + "(b" + " " * 1900,
            "## " + "(" * 3990,
        ):
            started = time.monotonic()
            hf.parse(f"{line}\n\nWords.\n" * 20)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_headings_with_odd_brackets_are_reported_not_guessed(self) -> None:
        for heading in ("## Name (kind", "## Name kind)", "## Name ((item))"):
            parsed = hf.parse(f"{heading}\n\nWords.\n")
            self.assertEqual(parsed.entries, (), heading)
            self.assertEqual(len(parsed.problems), 1, heading)

    def test_a_very_long_heading_is_not_an_entry(self) -> None:
        parsed = hf.parse("## " + "n" * 400 + " (item)\n\nWords.\n")
        self.assertEqual(parsed.entries, ())
