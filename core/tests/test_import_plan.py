"""What an imported transcript keeps (#2105). Invented fixtures only."""

from __future__ import annotations

import unittest

from dmbot.transcript import import_plan as ip
from dmbot.transcript import importer

ALICE, BOB, BOTID, STRANGER = 101, 102, 103, 104

CRAIG = """Craig Recording of Tuesday
Started at 18:00

Mira (mira_v): We open the door and listen.
{non-game context}
Tomas (tomas#1): I check the lock, it is rusty.
Mira (mira_v): Good, we go in. Then the stairs go down for a long time and we follow them.
Rook: The bot says hello.
"""

TIMED = """[0:00:05] Mira: First line here.
[0:01:30] Tomas: Second line here with a few more words in it.
[0:02:00] Mira: Third.
"""

DMBOT = """DMbot transcript: Tuesday
[0:42:10] (Mia) {Cerric}: I draw my sword.
[0:42:20] (Tomas): We follow.
[0:42:30] (DMbot): This is not the table's.
"""

MAPPING: dict[str, int | str | None] = {
    "Mira (mira_v)": ALICE,
    "Tomas (tomas#1)": BOB,
    "Rook": BOTID,
}


class TheFormats(unittest.TestCase):
    def test_craig_text_is_read_and_its_placeholder_counted(self) -> None:
        t = importer.parse(CRAIG)
        self.assertEqual(t.format, importer.PLAIN)
        self.assertEqual(
            [(s.label, s.lines) for s in ip.speakers(t)],
            [("Mira (mira_v)", 2), ("Tomas (tomas#1)", 1), ("Rook", 1)],
        )
        self.assertEqual(t.cut_out, 1)

    def test_a_timestamped_file_is_read(self) -> None:
        t = importer.parse(TIMED)
        self.assertEqual([s.label for s in ip.speakers(t)], ["Mira", "Tomas"])
        self.assertTrue(t.timed)

    def test_dmbots_own_download_is_read(self) -> None:
        t = importer.parse(DMBOT)
        self.assertEqual(t.format, importer.DMBOT)
        self.assertEqual([s.label for s in ip.speakers(t)], ["Mia", "Tomas"])

    def test_something_else_is_refused(self) -> None:
        with self.assertRaises(importer.TranscriptError):
            importer.parse("just some prose with no speakers at all\nand another line\n")


class TheConsentMapping(unittest.TestCase):
    def plan(self, **kw: object) -> ip.Plan:
        base: dict[str, object] = {"agreed": {ALICE, BOB}, "bots": {BOTID}}
        return ip.make_plan(importer.parse(CRAIG), MAPPING, **{**base, **kw})  # type: ignore[arg-type]

    def test_only_members_who_agreed_keep_their_lines(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.kept, 3)
        self.assertEqual({line.user_id for line in plan.lines}, {ALICE, BOB})
        self.assertEqual(plan.left_out, 1)

    def test_an_unmapped_or_dropped_speaker_is_dropped_before_anything_is_stored(self) -> None:
        mappings: list[dict[str, int | str | None]] = [
            {**MAPPING, "Tomas (tomas#1)": ip.DROP},
            {k: v for k, v in MAPPING.items() if k != "Tomas (tomas#1)"},
        ]
        for mapping in mappings:
            plan = ip.make_plan(importer.parse(CRAIG), mapping, agreed={ALICE, BOB}, bots={BOTID})
            self.assertNotIn(BOB, {line.user_id for line in plan.lines})
            tomas = next(r for r in plan.speakers if r.label.startswith("Tomas"))
            self.assertEqual((tomas.kept, tomas.dropped[ip.NOT_MATCHED]), (0, 1))

    def test_a_member_who_has_not_agreed_is_dropped(self) -> None:
        plan = self.plan(agreed={ALICE})  # Bob agreed under old terms or not at all
        self.assertEqual({line.user_id for line in plan.lines}, {ALICE})
        bob = next(r for r in plan.speakers if r.label.startswith("Tomas"))
        self.assertEqual(bob.dropped[ip.NOT_AGREED], 1)

    def test_a_bot_is_dropped_even_if_it_somehow_agreed(self) -> None:
        plan = self.plan(agreed={ALICE, BOB, BOTID})
        self.assertNotIn(BOTID, {line.user_id for line in plan.lines})
        rook = next(r for r in plan.speakers if r.label == "Rook")
        self.assertEqual(rook.dropped[ip.BOT], 1)

    def test_a_mapping_to_someone_outside_the_server_is_dropped(self) -> None:
        plan = self.plan(members={ALICE})
        self.assertEqual({line.user_id for line in plan.lines}, {ALICE})

    def test_a_nonsense_mapping_value_is_not_a_member(self) -> None:
        mapping: dict[str, int | str | None] = {**MAPPING, "Tomas (tomas#1)": True}
        plan = ip.make_plan(importer.parse(CRAIG), mapping, agreed={ALICE, BOB, 1})
        self.assertNotIn(1, {line.user_id for line in plan.lines})

    def test_every_line_is_counted_exactly_once(self) -> None:
        plan = self.plan(agreed={ALICE})
        total = sum(r.kept + sum(r.dropped.values()) for r in plan.speakers)
        self.assertEqual(total, 4)

    def test_the_plan_holds_no_words_but_the_kept_lines(self) -> None:
        plan = self.plan(agreed=set())
        self.assertEqual(plan.lines, ())


class TheLineLength(unittest.TestCase):
    def test_one_very_long_line_is_stored_as_several(self) -> None:
        words = " ".join(["word"] * 1_500)  # 7,499 characters
        t = importer.parse(f"Mira: {words}\nTomas: short line here\n")
        plan = ip.make_plan(t, {"Mira": ALICE, "Tomas": BOB}, agreed={ALICE, BOB})
        mira = [line for line in plan.lines if line.user_id == ALICE]
        self.assertGreaterEqual(len(mira), 4)
        self.assertTrue(all(len(line.text) <= ip.MAX_LINE_CHARS for line in mira))
        self.assertEqual(" ".join(line.text for line in mira), words)  # nothing lost
        starts = [line.started_ms for line in plan.lines]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(next(r for r in plan.speakers if r.label == "Mira").kept, 1)  # one line

    def test_a_word_longer_than_the_limit_is_cut_where_it_stands(self) -> None:
        pieces = ip._pieces("x" * 4_500)
        cap = ip.MAX_LINE_CHARS
        self.assertEqual([len(p) for p in pieces], [cap, cap, 4_500 - 2 * cap])


class TheTimes(unittest.TestCase):
    def test_a_file_without_times_gets_them_from_the_words(self) -> None:
        t = importer.parse(CRAIG)
        times = ip.timed(t)
        self.assertEqual(times[0][0], 0)
        self.assertEqual(times[1][0], times[0][0] + times[0][1])  # one after the other
        self.assertTrue(all(length > 0 for _, length in times))

    def test_a_file_with_times_keeps_them_and_never_overlaps_the_next_line(self) -> None:
        t = importer.parse(TIMED)
        times = ip.timed(t)
        self.assertEqual([start for start, _ in times], [5_000, 90_000, 120_000])
        self.assertLessEqual(times[1][0] + times[1][1], times[2][0])

    def test_lines_start_at_the_session_start_plus_their_time(self) -> None:
        plan = ip.make_plan(
            importer.parse(TIMED),
            {"Mira": ALICE, "Tomas": BOB},
            agreed={ALICE, BOB},
            base_ms=1_000_000,
        )
        self.assertEqual(
            [line.started_ms for line in plan.lines], [1_005_000, 1_090_000, 1_120_000]
        )


if __name__ == "__main__":
    unittest.main()
