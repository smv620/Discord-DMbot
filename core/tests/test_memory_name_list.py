"""The plain-text names list: the template, reading a list, and a download (pure)."""

import unittest
from pathlib import Path

from dmbot.memory.name_list import HEADER, TEMPLATE, OutName, parse, render
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
        from dmbot.memory.name_list import check_name

        self.assertIsNone(check_name("Hrothgar the Bold"))
        self.assertIn("|", check_name("Hrothgar | NPC") or "")
        self.assertIn("8 words", check_name("one two three four five six seven eight nine") or "")
        self.assertIn("link", check_name("www.example.com") or "")

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
