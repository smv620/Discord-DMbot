"""The plain-text names list: the template, reading a list, and a download (pure)."""

import unittest
from pathlib import Path

from dmbot.memory.name_list import HEADER, TEMPLATE, OutName, parse, render


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


if __name__ == "__main__":
    unittest.main()


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
