"""The plain-text names list: the template, reading a list, and a download (pure)."""

import time
import unittest
from pathlib import Path

from dmbot.memory.name_list import (
    HEADER,
    MAX_PER_LINE,
    TEMPLATE,
    TOO_MANY_OTHERS,
    TOO_MANY_SECRETS,
    OutName,
    parse,
    render,
)
from dmbot.memory.sounds import sound_codes
from dmbot.transcript.cleaner import likeness


class Template(unittest.TestCase):
    def test_the_template_explains_itself_in_comments_and_reads_back(self) -> None:
        head = TEMPLATE.split("Belleros |")[0].splitlines()
        self.assertTrue(all(line.startswith("###") for line in head if line))
        self.assertIn("name | kind | other names | secret names", HEADER)
        parsed = parse(TEMPLATE, secrets=True)
        self.assertEqual(parsed.refused, [])
        names = {line.name: line for line in parsed.lines}
        self.assertEqual(
            list(names),
            ["Belleros", "Bryn Shander", "Frostwolf tribe", "Heartseeker", "Auril", "Ulfgar"],
        )
        self.assertEqual(names["Belleros"].kind, "npc")
        self.assertEqual(names["Belleros"].others, ("Bell", "the old knight"))
        self.assertEqual(names["Belleros"].secrets, ("the hooded stranger",))
        self.assertEqual(names["Frostwolf tribe"].kind, "faction")
        self.assertEqual(names["Auril"].kind, "deity")
        self.assertIsNone(names["Ulfgar"].kind)  # no kind: DMbot asks later
        self.assertTrue(all(ord(c) < 0x2000 for c in TEMPLATE))  # no emoji in a text file


class Reading(unittest.TestCase):
    def test_kinds_in_plain_words_and_plurals(self) -> None:
        parsed = parse("A | Places\nB | monster\nC | wizard\nD | other", secrets=False)
        kinds = [(line.name, line.kind) for line in parsed.lines]
        self.assertEqual(kinds, [("A", "place"), ("B", "creature"), ("C", None), ("D", "concept")])

    def test_notes_descriptions_and_extra_columns_are_refused(self) -> None:
        text = "\n".join(
            [
                "# a note",
                "Ulfgar | NPC | Ulf | | chief of the tribe",  # 5 parts
                "Ulfgar is the old chief of the Frostwolf tribe in the north",  # a sentence
                " | place",  # no name
                "x" * 101,
            ]
        )
        parsed = parse(text, secrets=True)
        self.assertEqual(parsed.lines, [])
        self.assertEqual([n for n, _ in parsed.refused], [2, 3, 4, 5])

    def test_one_typed_name_is_checked_with_the_same_rules(self) -> None:
        from dmbot.memory import name_list as nl
        from dmbot.memory.name_list import check_name

        self.assertIsNone(check_name("Hrothgar the Bold"))
        self.assertEqual(check_name("Hrothgar | NPC"), nl.BAR)
        self.assertEqual(
            check_name("one two three four five six seven eight nine"), nl.TOO_MANY_WORDS
        )
        self.assertEqual(check_name("www.example.com"), nl.LINK)
        self.assertEqual(check_name("  "), nl.EMPTY)
        self.assertEqual(check_name("x" * 61, 60), nl.TOO_LONG)
        self.assertEqual(check_name("Ma\x07rin"), nl.UNREADABLE)

    def test_a_link_is_never_a_name(self) -> None:
        text = "\n".join(
            [
                "https://docs.google.com/document/d/1lHeFJAheOyz/edit?usp=drivesdk",
                "www.dndbeyond.com/sources",
                "docs.google.com/document/d/1lHeFJAheOyz/edit",
                "Ulfgar | NPC | https://example.com/ulfgar",
                "Belleros",
            ]
        )
        parsed = parse(text, secrets=True)
        self.assertEqual([line.name for line in parsed.lines], ["Belleros"])
        self.assertEqual([n for n, _ in parsed.refused], [1, 2, 3, 4])
        self.assertIn("Paste a link", parsed.refused[0][1])

    def test_only_a_dm_adds_secret_names_and_repeats_are_counted(self) -> None:
        parsed = parse("Belleros | npc | | the hooded stranger\nAuril\nauril", secrets=False)
        self.assertEqual([line.name for line in parsed.lines], ["Auril"])
        self.assertIn("only the campaign's DM", parsed.refused[0][1])
        self.assertEqual(parsed.repeated, 1)

    def test_other_names_never_repeat_the_name(self) -> None:
        (line,) = parse(
            "Belleros | npc | belleros; Bell; bell | Bell; the stranger", secrets=True
        ).lines
        self.assertEqual((line.others, line.secrets), (("Bell",), ("the stranger",)))

    def test_up_to_20_other_and_secret_names_on_a_line(self) -> None:
        many = "; ".join(f"Bell {i}" for i in range(MAX_PER_LINE))
        hoods = many.replace("Bell", "Hood")
        (line,) = parse(f"Belleros | npc | {many} | {hoods}", secrets=True).lines
        self.assertEqual((len(line.others), len(line.secrets)), (20, 20))
        text = f"Belleros | npc | {many}; Bell 20\nAuril | god | | {many}; Bell 20"
        parsed = parse(text, secrets=True)
        self.assertEqual(parsed.lines, [])
        self.assertEqual(
            parsed.refused,
            [
                (1, TOO_MANY_OTHERS),
                (2, TOO_MANY_SECRETS),
            ],
        )
        self.assertIn("starts with the name again", TOO_MANY_OTHERS)
        split = parse(  # done as the refusals say: all kept
            f"Belleros | npc | {many}\nBelleros | | Bell 20\nBelleros | | | {hoods}\n"
            "Belleros | | | Hood 20",
            secrets=True,
        )
        self.assertEqual((len(split.lines[0].others), len(split.lines[0].secrets)), (21, 21))

    def test_a_name_on_many_lines_is_quick(self) -> None:
        # #598 perf-qa: each repeated line used to copy every name before it.
        many = "; ".join(f"Bell {i}" for i in range(MAX_PER_LINE))
        text = "".join(
            f"Belleros | | {many.replace('Bell', f'B{n}')} | {many.replace('Bell', f'H{n}')}\n"
            for n in range(1000)
        )
        started = time.monotonic()
        (line,) = parse(text, secrets=True).lines
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((len(line.others), len(line.secrets)), (20_000, 20_000))

    def test_the_same_name_written_again_is_not_more(self) -> None:
        (line,) = parse("Belleros | npc | " + "; ".join(["Bell"] * 30), secrets=True).lines
        self.assertEqual(line.others, ("Bell",))


class Download(unittest.TestCase):
    def test_a_download_reads_back_the_same(self) -> None:
        names = [
            OutName("Ulfgar", "npc", (), ()),
            OutName("Belleros", "npc", ("Bell",), ("the hooded stranger",)),
            OutName("Bryn Shander", "place", (), ()),
        ]
        text = render(names, campaign="Frostmaiden", secrets=True)
        self.assertIn("This file includes secret names", text)
        lines = parse(text, secrets=True).lines
        self.assertEqual([line.name for line in lines], ["Belleros", "Bryn Shander", "Ulfgar"])
        self.assertEqual(lines[0].secrets, ("the hooded stranger",))
        self.assertNotIn("hooded", render(names, campaign="Frostmaiden", secrets=False))

    def test_a_name_with_many_other_names_still_reads_back(self) -> None:
        others = tuple(f"Bell {i}" for i in range(45))
        hidden = tuple(f"Hood {i}" for i in range(5))
        names = [OutName("Belleros", "npc", others, hidden)]
        text = render(names, campaign="Frostmaiden", secrets=True)
        self.assertEqual(text.count("\nBelleros | NPC"), 3)  # 20, 20 and 5
        parsed = parse(text, secrets=True)
        self.assertEqual(parsed.refused, [])
        (line,) = parsed.lines
        self.assertEqual((line.others, line.secrets), (others, hidden))
        players = render(names, campaign="Frostmaiden", secrets=False)
        self.assertNotIn("Hood", players)
        self.assertEqual(parse(players, secrets=False).lines[0].others, others)


SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"


class BakeoffStoryNames(unittest.TestCase):
    """docs/test-scripts/bakeoff-story-names.txt, the owner's bulk-import test (#368)."""

    def test_the_test_file_reads_cleanly(self) -> None:
        parsed = parse((SCRIPTS / "bakeoff-story-names.txt").read_text(), secrets=False)
        self.assertEqual(parsed.refused, [])
        self.assertEqual(parsed.repeated, 0)
        self.assertEqual(len(parsed.lines), 26)  # the story's 29 names, less the 3 left out
        self.assertTrue(all(line.kind is not None for line in parsed.lines))
        by_name = {line.name: line for line in parsed.lines}
        self.assertEqual(by_name["Bell"].others, ("Belleros",))  # swapped on purpose
        self.assertEqual(by_name["Varrow"].kind, "place")  # a different kind on purpose
        for left_out in ("Cerric", "Mirelle", "Kael"):
            self.assertNotIn(left_out, by_name)

    def test_the_setup_block_reads_cleanly(self) -> None:
        text = (SCRIPTS / "bakeoff-story-names-setup.md").read_text()
        block = text.split("```")[1]
        parsed = parse(block, secrets=False)
        self.assertEqual(parsed.refused, [])
        self.assertEqual(len(parsed.lines), 10)

    def test_the_setup_note_matches_the_file(self) -> None:
        # The live test's expected numbers rest on these (bakeoff-story-names-setup.md).
        setup = {
            line.name: line
            for line in parse(
                (SCRIPTS / "bakeoff-story-names-setup.md").read_text().split("```")[1],
                secrets=False,
            ).lines
        }
        listed = {
            line.name: line
            for line in parse(
                (SCRIPTS / "bakeoff-story-names.txt").read_text(), secrets=False
            ).lines
        }
        for left_out in ("Cerric", "Mirelle", "Kael"):
            self.assertIn(left_out, setup)
        self.assertEqual(setup["Belleros"].others, ("Bell",))
        self.assertEqual(setup["Oskar Vane"].others, ("Vane",))
        self.assertEqual(listed["Vane"].others, ("Oskar Vane",))
        self.assertEqual((setup["Varrow"].kind, listed["Varrow"].kind), ("deity", "place"))
        self.assertEqual(
            (setup["Ashen Crown"].kind, listed["Ashen Crown"].kind), ("faction", "place")
        )

    def test_only_the_close_spellings_sound_like_known_names(self) -> None:
        known = ["Cerric", "Mirelle", "Kael", "Gorrak", "Quillon", "Brynwater", "Belleros", "Bell"]
        known += ["Oskar Vane", "Vane", "Varrow", "Ashen Crown"]
        codes = {code for name in known for code in sound_codes(name)}
        close = {"Gorrack": "Gorrak", "Quilon": "Quillon", "Brynnwater": "Brynwater"}
        for near, original in close.items():
            self.assertTrue(set(sound_codes(near)) & set(sound_codes(original)), near)
            self.assertTrue(0.9 <= likeness(near, original) <= 0.95, near)
        lines = parse((SCRIPTS / "bakeoff-story-names.txt").read_text(), secrets=False).lines
        exact = {"Bell", "Vane", "Varrow", "Ashen Crown"}
        for line in lines:
            if line.name in close or line.name in exact:
                continue
            self.assertFalse(set(sound_codes(line.name)) & codes, line.name)


if __name__ == "__main__":
    unittest.main()
