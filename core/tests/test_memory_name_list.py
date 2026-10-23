"""The plain-text names list: the template, reading a list, and a download (pure)."""

import time
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory.name_list import (
    ASIDE,
    HEADER,
    MAX_ADDED,
    MAX_FILE_BYTES,
    MAX_LINES,
    MAX_NAMES,
    MAX_PER_LINE,
    NEAR_SOUND,
    SOUND,
    TEMPLATE,
    TOO_MANY_OTHERS,
    TOO_MANY_SECRETS,
    OutName,
    OutPerson,
    header,
    judge_bracket,
    lines_for,
    parse,
    render,
    render_files,
    split_bracket,
)
from dmbot.memory.sounds import sound_codes
from dmbot.transcript.cleaner import likeness
from dmbot.ui.name_lists import read_upload


class Pronunciation(unittest.TestCase):
    """The bracket after a name (#2447): a respelling, an aside, or a maybe. Pure code."""

    def read(self, line: str) -> Any:
        parsed = parse(line, secrets=True)
        self.assertEqual(parsed.refused, [])
        (one,) = parsed.lines
        return one

    def test_a_respelling_is_how_to_say_the_name_not_another_name(self) -> None:
        one = self.read("Tavin (TAV-in) | npc")
        self.assertEqual((one.name, one.said_as, one.ask, one.others), ("Tavin", "TAV-in", "", ()))
        one = self.read("Ka'zeth (kah-ZETH) | npc | Kaz")
        self.assertEqual((one.name, one.said_as, one.others), ("Ka'zeth", "kah-ZETH", ("Kaz",)))

    def test_words_in_the_bracket_make_it_an_aside_kept_as_another_name(self) -> None:
        one = self.read("Tavin (the rogue) | npc")
        self.assertEqual((one.name, one.said_as, one.ask), ("Tavin", "", ""))
        self.assertEqual(one.others, ("Tavin the rogue",))
        one = self.read("Vex (the Ashen Hand's boss) | npc")
        self.assertEqual((one.name, one.others), ("Vex", ("Vex the Ashen Hand's boss",)))

    def test_a_plain_word_beats_a_good_sound(self) -> None:
        # "A" and "of" are plain English: an aside, even though the rest sounds like the name
        self.assertEqual(self.read("Tavin (a TAV-in) | npc").said_as, "")
        self.assertEqual(self.read("Tavin (a TAV-in) | npc").others, ("Tavin a TAV-in",))

    def test_a_near_miss_asks_and_changes_nothing_until_answered(self) -> None:
        one = self.read("Jorvik (YOR-vick) | npc")
        self.assertEqual(
            (one.name, one.said_as, one.ask, one.others), ("Jorvik", "", "YOR-vick", ())
        )

    def test_a_look_alike_without_a_respelling_mark_is_asked_never_accepted_silently(self) -> None:
        for name, inside in (("Kaela", "Kael"), ("Jon", "John"), ("Sera", "Sara"), ("Vex", "Vix")):
            self.assertNotEqual(judge_bracket(name, inside), SOUND, (name, inside))
        self.assertEqual(judge_bracket("Tavin", "TAV-in"), SOUND)
        self.assertEqual(judge_bracket("Ka'zeth", "kah-ZETH"), SOUND)
        self.assertEqual(judge_bracket("Tavin", "TAVin"), SOUND)  # a shouted syllable counts
        self.assertEqual(judge_bracket("Tavin", "tavin"), NEAR_SOUND)  # alike, but no mark
        self.assertEqual(judge_bracket("Tavin", "the rogue"), ASIDE)
        self.assertEqual(judge_bracket("Tavin", "rogue"), ASIDE)  # far from the name

    def test_brackets_must_be_a_matching_pair(self) -> None:
        self.assertIsNone(split_bracket("Tavin (TAV-in]"))
        self.assertEqual(split_bracket("Tavin [TAV-in]"), ("Tavin", "TAV-in"))

    def test_the_first_line_that_gives_a_pronunciation_decides(self) -> None:
        for text, said, ask in (
            ("Tavin (the rogue) | npc\nTavin (TAV-in) | npc", "TAV-in", ""),
            ("Tavin (TAH-veen) | npc\nTavin (TAV-in) | npc", "TAV-in", ""),
            ("Tavin (TAV-in) | npc\nTavin (TAY-vin) | npc", "TAV-in", ""),
            ("Tavin (TAH-veen) | npc\nTavin (tahveen) | npc", "", "TAH-veen"),
        ):
            (one,) = parse(text, secrets=True).lines
            self.assertEqual((one.said_as, one.ask), (said, ask), text)

    def test_a_split_name_remembers_the_whole_one(self) -> None:
        (one,) = parse("Fenn (Lower Quay) | place", secrets=True).lines
        self.assertEqual(
            (one.name, one.whole, one.others), ("Fenn", "Fenn (Lower Quay)", ("Fenn Lower Quay",))
        )

    def test_every_kind_of_bracket_remembers_the_whole_name(self) -> None:
        for text in ("Sera (SEH-rah)", "Sera (Sara)", "Sera (the old one)"):
            (one,) = parse(f"{text} | npc", secrets=True).lines
            self.assertEqual((one.name, one.whole), ("Sera", text), text)

    def test_an_aside_with_a_comma_stays_one_name(self) -> None:
        one = self.read("Vex (the boss, retired) | npc | Vexa")
        self.assertEqual((one.name, one.others), ("Vex", ("Vex the boss, retired", "Vexa")))

    def test_a_download_uploaded_again_gives_the_same_names(self) -> None:
        """Other and secret names with a comma, a semicolon or a quote in them survive (#2482)."""
        others = ("Vex the boss, retired", "Vexa; the Younger", '"Vexx"', 'Old "Vex"', "Plain")
        secrets = ("Mr. Black, the broker", 'He said "no", twice')
        text = "\n".join(lines_for("Vex", "npc", others, secrets))
        (one,) = parse(text, secrets=True).lines
        self.assertEqual((one.others, one.secrets), (others, secrets), text)

    def test_a_player_row_with_separators_in_its_names_comes_back_the_same(self) -> None:
        from dmbot.memory.name_list import OutPerson, person_lines

        for names in (("Bob, the Bold", "Bobby; B"), ('"Bob"', 'The "Bob"'), ("Bob",)):
            text = "\n".join(
                person_lines(OutPerson(123456789012345678, names, ("Vex, Jr.",), "Player 1234"))
            )
            (back,) = parse(text, secrets=True).people
            self.assertEqual((back.names, back.plays), (names, ("Vex, Jr.",)), text)

    def test_more_names_than_a_line_holds_still_read_back_as_one_entry(self) -> None:
        others = tuple(f"Alias {n}, the {n}th" for n in range(MAX_PER_LINE + 1))
        text = "\n".join(lines_for("Vex", "npc", others, ()))
        self.assertEqual(len(text.splitlines()), 2)
        (one,) = parse(text, secrets=True).lines
        self.assertEqual(one.others, others)

    def test_an_empty_quoted_piece_is_dropped(self) -> None:
        self.assertEqual(self.read('Vex | npc | "", "Vexa" ;').others, ("Vexa",))

    def test_quotes_that_do_not_wrap_a_whole_name_stay_as_they_were(self) -> None:
        one = self.read('Vex | npc | Old "Vex", "Vexa" extra, "open')
        self.assertEqual(one.others, ('Old "Vex"', '"Vexa" extra', '"open'))
        one = self.read('Vex | npc | "Vexa, the Younger", Vexi')
        self.assertEqual(one.others, ("Vexa, the Younger", "Vexi"))

    def test_no_bracket_and_a_bracket_not_at_the_end_are_left_alone(self) -> None:
        self.assertEqual(self.read("Tavin | npc").said_as, "")
        one = self.read("The (old) Mill | place")
        self.assertEqual((one.name, one.said_as, one.ask), ("The (old) Mill", "", ""))

    def test_the_first_line_for_a_name_decides_its_pronunciation(self) -> None:
        parsed = parse("Tavin (TAV-in) | npc\nTavin (the rogue) | npc", secrets=True)
        (one,) = parsed.lines
        self.assertEqual((one.said_as, one.others), ("TAV-in", ("Tavin the rogue",)))


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

    def test_the_total_says_what_it_counts(self) -> None:
        # The same words as the "too many" message (#598): secret names count too.
        self.assertIn("5,000 new other and secret names in all.", header(secrets=True))
        self.assertIn("5,000 new other names in all.", header(secrets=False))


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
        self.assertLess(time.monotonic() - started, 10)  # room for parallel tests (#1323)
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


class BigDownload(unittest.TestCase):
    """A download that is too big for one upload comes as several files, each of which
    uploads again (#685)."""

    @staticmethod
    def campaign(count: int, *, others: int = 1) -> list[OutName]:
        return [
            OutName(f"Name {i:05}", "npc", tuple(f"Nick {i:05} {k}" for k in range(others)), ())
            for i in range(count)
        ]

    def test_a_small_campaign_is_one_file_exactly_as_before(self) -> None:
        names = self.campaign(30)
        self.assertEqual(
            render_files(names, campaign="Frostmaiden", secrets=True),
            [render(names, campaign="Frostmaiden", secrets=True)],
        )

    def test_every_file_of_a_full_campaign_passes_the_uploads_own_limits(self) -> None:
        names = self.campaign(MAX_NAMES)
        files = render_files(names, campaign="Frostmaiden", secrets=True)
        self.assertGreater(len(files), 1)
        for text in files:
            self.assertLessEqual(len(text.splitlines()), MAX_LINES)  # notes count
            self.assertLessEqual(len(text.encode()), MAX_FILE_BYTES)
            self.assertIn("name | kind | other names | secret names", text)  # valid alone

    def test_the_files_restore_every_name_each_under_the_add_cap(self) -> None:
        names = self.campaign(MAX_NAMES, others=2)
        seen: dict[str, tuple[str, ...]] = {}
        files = render_files(names, campaign="Frostmaiden", secrets=True)
        for text in reversed(files):  # in any order
            parsed = parse(text, secrets=True)
            self.assertEqual(parsed.refused, [])
            added = sum(len(line.others) + len(line.secrets) for line in parsed.lines)
            self.assertLessEqual(added, MAX_ADDED)
            for line in parsed.lines:
                self.assertNotIn(line.name, seen)  # a name is in one file only
                seen[line.name] = line.others
        self.assertEqual(seen, {n.name: n.others for n in names})

    def test_a_file_holding_many_other_names_each_stays_under_the_add_cap(self) -> None:
        names = self.campaign(1900, others=4)  # fits the size limits, but 7,600 adds
        files = render_files(names, campaign="Frostmaiden", secrets=False)
        self.assertGreater(len(files), 1)
        for text in files:
            parsed = parse(text, secrets=False)
            self.assertEqual(parsed.refused, [])
            self.assertLessEqual(sum(len(x.others) for x in parsed.lines), MAX_ADDED)

    def test_secret_names_count_toward_the_add_cap_of_each_file(self) -> None:
        names = [
            OutName(f"Name {i:05}", "npc", ("a", "b", "c"), ("x", "y", "z")) for i in range(1000)
        ]
        files = render_files(names, campaign="Frostmaiden", secrets=True)
        self.assertGreater(len(files), 1)  # 6,000 added is more than one upload takes
        for text in files:
            parsed = parse(text, secrets=True)
            self.assertEqual(parsed.refused, [])
            added = sum(len(x.others) + len(x.secrets) for x in parsed.lines)
            self.assertLessEqual(added, MAX_ADDED)

    def test_a_name_on_several_lines_that_would_pass_the_line_limit_opens_the_next_file(
        self,
    ) -> None:
        from dmbot.memory.name_list import _prelude

        base = "\n".join(_prelude(campaign="Frostmaiden", secrets=False, part=None))
        room = MAX_LINES - (base.count("\n") + 1)
        many = tuple(f"Bell {i}" for i in range(45))  # three lines
        names = [
            OutName(f"Name {i:05}", "npc", (), ()) for i in range(room - 2)
        ]  # 2 lines left, 1 once the part note is counted
        names.append(OutName("Zzz Belleros", "npc", many, ()))
        files = render_files(names, campaign="Frostmaiden", secrets=False)
        self.assertEqual(len(files), 2)
        for text in files:
            self.assertLessEqual(len(text.splitlines()), MAX_LINES)
        self.assertNotIn("Zzz Belleros", files[0])
        self.assertEqual(files[1].count("\nZzz Belleros | NPC"), 3)

    @staticmethod
    def _tuned(count: int, pad: int) -> list[OutName]:
        # Lines of one size (about 500 bytes, so the line limit stays far off), plus one
        # last name whose length moves the total byte by byte.
        others = tuple(f"{j}" + "o" * 100 for j in range(5))
        names = [OutName(f"N{i:07}", "npc", others, ()) for i in range(count)]
        return [*names, OutName("Tuner " + "x" * pad, "npc", (), ())]

    def test_a_file_of_exactly_the_size_limit_is_one_file_and_one_byte_more_is_two(self) -> None:
        count = 400
        while len(render(self._tuned(count + 1, 0), campaign="C", secrets=False).encode()) <= (
            MAX_FILE_BYTES
        ):
            count += 1  # the most lines that fit, leaving less than a line of room
        size = len(render(self._tuned(count, 0), campaign="C", secrets=False).encode())
        pad = MAX_FILE_BYTES - size
        self.assertTrue(0 <= pad < 600)
        files = render_files(self._tuned(count, pad), campaign="C", secrets=False)
        self.assertEqual(len(files), 1)
        self.assertEqual(len(files[0].encode()), MAX_FILE_BYTES)  # incl. the final newline
        over = render_files(self._tuned(count, pad + 1), campaign="C", secrets=False)
        self.assertEqual(len(over), 2)

    def test_every_file_fits_however_the_slack_falls(self) -> None:
        # The last name moves the slack through every position, including the narrow band
        # between the "file i of n" notes' size and one line's size, where leaving the notes
        # out of the plan would push a file over the limit.
        for pad in range(0, 600, 3):
            self._assert_every_file_is_accepted(self._tuned(1500, pad))

    def test_the_widest_file_numbers_fit_too(self) -> None:
        # Ten or more files make the numbers a digit wider ("10 of 12"). Lines of 218 bytes
        # fit 1,193 to a file if the notes were "1 of 1", but only 1,192 with the real
        # ones: the plan must leave room for the widest, or the 10th file is a byte over.
        names = [OutName(f"N{i:07}", "npc", ("o" * 200,), ()) for i in range(13_000)]
        files = self._assert_every_file_is_accepted(names)
        self.assertGreaterEqual(len(files), 10)

    def _assert_every_file_is_accepted(self, names: list[OutName]) -> list[str]:
        files = render_files(names, campaign="C", secrets=False)
        self.assertGreater(len(files), 1)
        for text in files:
            self.assertLessEqual(len(text.encode()), MAX_FILE_BYTES)
            _, refused = read_upload(text.encode())  # and the upload itself accepts it
            self.assertIsNone(refused)
        return files

    def test_no_names_is_one_header_only_file(self) -> None:
        files = render_files([], campaign="Frostmaiden", secrets=False)
        self.assertEqual(files, [render([], campaign="Frostmaiden", secrets=False)])

    def test_multibyte_names_are_counted_in_bytes(self) -> None:
        names = [OutName(f"日本{i:05}", "npc", ("é" * 40,) * 5, ()) for i in range(3000)]
        files = render_files(names, campaign="Frostmaiden", secrets=False)
        self.assertGreater(len(files), 1)
        for text in files:
            self.assertLessEqual(len(text.encode()), MAX_FILE_BYTES)

    def test_players_files_never_mention_secret_names(self) -> None:
        names = [OutName(f"Name {i:05}", "npc", ("Nick",), ("Hidden Hood",)) for i in range(4000)]
        files = render_files(names, campaign="Frostmaiden", secrets=False)
        self.assertGreater(len(files), 1)
        for text in files:
            self.assertNotIn("Hidden Hood", text)
            self.assertNotIn("secret", text.lower())

    def test_a_name_on_several_lines_stays_whole_in_one_file(self) -> None:
        many = tuple(f"Bell {i}" for i in range(45))  # three lines
        names = [OutName(f"Name {i:05}", "npc", (), ()) for i in range(1990)]
        names.insert(1000, OutName("Name 00999b", "npc", many, ("Hood",)))
        files = render_files(names, campaign="Frostmaiden", secrets=True)
        holding = [f for f in files if "Name 00999b | NPC" in f]
        self.assertEqual(len(holding), 1)
        self.assertEqual(holding[0].count("\nName 00999b | NPC"), 3)
        (line,) = [x for x in parse(holding[0], secrets=True).lines if x.name == "Name 00999b"]
        self.assertEqual((line.others, line.secrets), (many, ("Hood",)))

    def test_long_names_split_on_size_before_the_line_count(self) -> None:
        long = tuple(f"{'x' * 90} {k:02}" for k in range(20))  # a line of about 2 KB
        names = [OutName(f"{'N' * 80} {i:04}", "npc", long, ()) for i in range(300)]
        files = render_files(names, campaign="Frostmaiden", secrets=False)
        self.assertGreater(len(files), 1)
        for text in files:
            self.assertLessEqual(len(text.encode()), MAX_FILE_BYTES)
            self.assertLess(len(text.splitlines()), MAX_LINES)
        restored = [line.name for text in files for line in parse(text, secrets=False).lines]
        self.assertEqual(sorted(restored), sorted(n.name for n in names))

    def test_each_file_says_which_one_it_is(self) -> None:
        files = render_files(self.campaign(5000), campaign="Frostmaiden", secrets=False)
        for i, text in enumerate(files, 1):
            self.assertIn(f"Names DMbot knows for Frostmaiden (file {i} of {len(files)})", text)


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


class DownloadText(unittest.TestCase):
    def test_a_download_says_how_to_add_it_again_not_to_change_examples(self) -> None:
        for secrets in (True, False):
            text = render([OutName("Belleros", "npc", (), ())], campaign="F", secrets=secrets)
            self.assertIn("To add it again: edit it if you like", text)
            self.assertIn("Add many > Upload a file", text)
            self.assertNotIn("change the examples", text)
            self.assertNotIn("Paste a list", text)  # too long to paste near the limit

    def test_every_file_of_a_big_download_says_so(self) -> None:
        names = [OutName(f"Name {i:05}", "npc", ("o" * 130,), ()) for i in range(6_000)]
        for text in render_files(names, campaign="F", secrets=False):
            self.assertIn("To add it again", text)
            self.assertNotIn("change the examples", text)

    def test_the_template_still_tells_people_to_change_its_examples(self) -> None:
        self.assertIn("change the examples to your own names", TEMPLATE)


class LineSeparators(unittest.TestCase):
    def test_a_name_with_a_line_or_paragraph_separator_is_refused(self) -> None:
        from dmbot.memory.name_list import UNREADABLE, check_name

        for sep in ("\u2028", "\u2029", "\x85", "\x0b"):
            self.assertEqual(check_name(f"Bel{sep}leros"), UNREADABLE, repr(sep))

    def test_a_name_saved_before_the_rule_is_written_on_one_line(self) -> None:
        text = render(
            [OutName("Bel\u2028leros", "npc", ("the\u2029knight",), ("a\u2028b",))],
            campaign="F",
            secrets=True,
        )
        self.assertEqual(len(text.splitlines()), text.count("\n"))  # no hidden line breaks
        self.assertIn("Bel leros | NPC | the knight | a b", text)


WHO = 111111111111111234


class People(unittest.TestCase):
    """Player or DM rows (#1559): for the campaign's DMs only, with the Discord id."""

    def test_a_row_is_read_for_a_dm_with_names_and_characters(self) -> None:
        parsed = parse(
            f"Mike | player or DM | Mikey; Mike S | | {WHO} | Cerric; Maren", secrets=True
        )
        self.assertEqual(parsed.refused, [])
        (row,) = parsed.people
        self.assertEqual(
            (row.who, row.names, row.plays), (WHO, ("Mike", "Mikey", "Mike S"), ("Cerric", "Maren"))
        )

    def test_a_number_too_big_for_an_id_is_refused(self) -> None:
        for who in ("1" * 20, "9" * 19):
            self.assertEqual(parse(f"Mike | player or DM | | | {who}", secrets=True).people, [])

    def test_a_download_holds_fifty_people_a_file_so_each_uploads_again(self) -> None:
        people = [OutPerson(WHO + i, (f"P{i}",), (), "x") for i in range(120)]
        files = render_files([], campaign="Frost", secrets=True, people=people)
        self.assertGreater(len(files), 1)
        for text in files:
            parsed = parse(text, secrets=True)
            self.assertEqual(parsed.refused, [])
            self.assertLessEqual(len(parsed.people), 50)
        self.assertEqual({r.who for t in files for r in parse(t, secrets=True).people},
                         {WHO + i for i in range(120)})  # fmt: skip

    def test_the_same_person_on_two_lines_is_one(self) -> None:
        text = f"Mike | player or DM | | | {WHO}\nMikey | player or DM | Mike | | {WHO} | Cerric"
        (row,) = parse(text, secrets=True).people
        self.assertEqual((row.names, row.plays), (("Mike", "Mikey"), ("Cerric",)))

    def test_a_non_dm_or_a_row_without_a_good_id_is_refused(self) -> None:
        for text, secrets in (
            (f"Mike | player or DM | | | {WHO}", False),  # not a DM: as before
            ("Mike | player or DM", True),
            ("Mike | player or DM | | | 12345", True),
            ("Mike | player or DM | | | abc", True),
            (f"Mike | player or DM | | hidden | {WHO}", True),  # no secret names for a person
        ):
            parsed = parse(text, secrets=secrets)
            self.assertEqual((parsed.people, len(parsed.refused)), ([], 1), text)

    def test_the_fifth_and_sixth_parts_are_only_for_people(self) -> None:
        parsed = parse(f"Bell | npc | | | {WHO}", secrets=True)
        self.assertEqual(len(parsed.refused), 1)
        self.assertEqual(parsed.lines, [])
        self.assertEqual(len(parse("a | b | c | d | e | f | g", secrets=True).refused), 1)

    def test_the_dm_words_are_never_a_table_name(self) -> None:
        (row,) = parse(f"Mike | player or DM | DM; GM | | {WHO}", secrets=True).people
        self.assertEqual(row.names, ("Mike",))

    def test_a_download_lists_people_for_a_dm_only_and_reads_back(self) -> None:
        people = [OutPerson(WHO, ("Mike", "Mikey"), ("Cerric",), "Player 1234")]
        names = [OutName("Ulfgar", "npc", (), ())]
        dm = render(names, campaign="Frost", secrets=True, people=people)
        self.assertIn(f"Mike | player or DM | Mikey |  | {WHO} | Cerric", dm)
        again = parse(dm, secrets=True)
        self.assertEqual(again.refused, [])
        self.assertEqual([(r.who, r.names, r.plays) for r in again.people],
                         [(WHO, ("Mike", "Mikey"), ("Cerric",))])  # fmt: skip
        other = render(names, campaign="Frost", secrets=False, people=people)
        self.assertNotIn(str(WHO), other)
        self.assertNotIn("player or DM", other)

    def test_a_person_with_no_table_name_is_written_with_the_placeholder(self) -> None:
        out = render(
            [], campaign="Frost", secrets=True, people=[OutPerson(WHO, (), (), "Player 1234")]
        )
        self.assertIn(f"Player 1234 | player or DM |  |  | {WHO}", out)

    def test_people_count_in_the_files_a_big_download_is_split_into(self) -> None:
        people = [OutPerson(WHO + i, (f"P{i}",), (), "x") for i in range(40)]
        files = render_files([], campaign="Frost", secrets=True, people=people)
        found = {r.who for text in files for r in parse(text, secrets=True).people}
        self.assertEqual(found, {WHO + i for i in range(40)})
