"""The off-topic filter's pure parts (#52), without Discord, a database or AI."""

import unittest

from dmbot.transcript.topics import (
    DM_LEAVE_OUT_S,
    GAME,
    KEEP_STRETCH_S,
    OFF_TOPIC,
    TABLE_TALK,
    Spoken,
    collapse,
    lean_to_keeping,
    marker,
    obviously_game,
)

MIA, DEE, DM = 8, 9, 7


class ObviouslyGameTest(unittest.TestCase):
    def test_game_talk_needs_no_ai(self) -> None:
        for text in ("Roll initiative, everyone", "I take 2d6 damage", "nat 20!", "That's a d20"):
            with self.subTest(text=text):
                self.assertTrue(obviously_game(text))

    def test_a_campaign_name_is_enough(self) -> None:
        self.assertTrue(obviously_game("Where did Belleros go?", named=1))

    def test_everyday_talk_goes_to_the_ai(self) -> None:
        for text in (
            "Is anyone else free next Thursday?",
            "Hang on, my dog is barking at the mail carrier.",
            "Did anybody watch the game last night?",
            "I have to leave at ten tonight",
        ):
            with self.subTest(text=text):
                self.assertFalse(obviously_game(text))

    def test_one_table_word_alone_isnt_enough(self) -> None:
        self.assertFalse(obviously_game("It's my turn to cook tonight"))


class MarkerTest(unittest.TestCase):
    def test_wording(self) -> None:
        self.assertEqual(marker(82), "[1m 22s of off-topic chat skipped]")
        self.assertEqual(marker(8.4), "[8s of off-topic chat skipped]")
        self.assertEqual(marker(0.2), "[1s of off-topic chat skipped]")
        self.assertEqual(marker(120), "[2m 0s of off-topic chat skipped]")


class CollapseTest(unittest.TestCase):
    def test_a_run_from_one_person_is_one_marker(self) -> None:
        lines = [
            Spoken(MIA, 0, 3.0, "We attack!", GAME),
            Spoken(DEE, 1000, 40.0, "free Thursday?", OFF_TOPIC),
            Spoken(DEE, 2000, 42.0, "or Friday?", OFF_TOPIC),
            Spoken(MIA, 3000, 2.0, "Roll it", TABLE_TALK),
        ]
        shown = list(collapse(lines))
        self.assertEqual(
            [(s.speaker, s.text, s.skipped) for s in shown],
            [
                (MIA, "We attack!", False),
                (DEE, "[1m 22s of off-topic chat skipped]", True),
                (MIA, "Roll it", False),  # table talk always stays
            ],
        )
        self.assertEqual(shown[1].started_ms, 1000)

    def test_someone_else_ends_the_run(self) -> None:
        lines = [
            Spoken(DEE, 0, 5.0, "a", OFF_TOPIC),
            Spoken(MIA, 1, 5.0, "b", OFF_TOPIC),
            Spoken(DEE, 2, 5.0, "c", OFF_TOPIC),
        ]
        self.assertEqual([s.speaker for s in collapse(lines)], [DEE, MIA, DEE])

    def test_nothing_hidden_keeps_every_line(self) -> None:
        lines = [Spoken(MIA, i, 1.0, f"line {i}") for i in range(3)]
        self.assertEqual([s.text for s in collapse(lines)], ["line 0", "line 1", "line 2"])


class LeanToKeepingTest(unittest.TestCase):
    """The filter leans to keeping (#2567). Lines here are made up."""

    def test_a_three_second_stretch_between_two_dm_game_lines_is_kept(self) -> None:
        lines = [
            Spoken(DM, 0, 5.0, "a", GAME),
            Spoken(DM, 6_000, 3.0, "b", OFF_TOPIC),
            Spoken(DM, 10_000, 5.0, "c", GAME),
        ]
        self.assertEqual([x.topic for x in lean_to_keeping(lines, {DM})], [GAME] * 3)

    def test_a_short_stretch_between_game_talk_is_kept_whoever_said_it(self) -> None:
        lines = [
            Spoken(MIA, 0, 4.0, "a", GAME),
            Spoken(DEE, 5_000, 8.0, "b", OFF_TOPIC),
            Spoken(MIA, 15_000, 3.0, "c", TABLE_TALK),
        ]
        self.assertEqual(lean_to_keeping(lines)[1].topic, GAME)

    def test_a_long_stretch_between_players_is_still_left_out(self) -> None:
        lines = [Spoken(MIA, 0, 4.0, "a", GAME)]
        lines += [Spoken(DEE, 5_000 + i * 8_000, 7.0, "x", OFF_TOPIC) for i in range(6)]
        lines.append(Spoken(MIA, 60_000, 3.0, "z", GAME))
        topics = [x.topic for x in lean_to_keeping(lines, {DM})]
        self.assertEqual(topics, [GAME] + [OFF_TOPIC] * 6 + [GAME])

    def test_a_stretch_at_the_edge_has_game_talk_on_one_side_only(self) -> None:
        lines = [Spoken(DEE, 0, 5.0, "x", OFF_TOPIC), Spoken(MIA, 6_000, 3.0, "z", GAME)]
        self.assertEqual(lean_to_keeping(lines)[0].topic, OFF_TOPIC)

    def test_the_dms_lines_stay_unless_the_whole_stretch_is_long(self) -> None:
        def stretch(seconds: float) -> list[Spoken]:
            return [Spoken(DM, 0, seconds, "x", OFF_TOPIC)]

        self.assertEqual(lean_to_keeping(stretch(DM_LEAVE_OUT_S - 1), {DM})[0].topic, GAME)
        self.assertEqual(lean_to_keeping(stretch(DM_LEAVE_OUT_S + 1), {DM})[0].topic, OFF_TOPIC)
        self.assertEqual(lean_to_keeping(stretch(DM_LEAVE_OUT_S - 1))[0].topic, OFF_TOPIC)

    def test_a_line_the_dm_decided_is_never_changed(self) -> None:
        lines = [
            Spoken(MIA, 0, 4.0, "a", GAME),
            Spoken(DEE, 5_000, 3.0, "b", OFF_TOPIC, decided=True),
            Spoken(MIA, 9_000, 3.0, "c", GAME),
        ]
        self.assertEqual(lean_to_keeping(lines)[1].topic, OFF_TOPIC)

    def test_it_is_the_same_when_run_again(self) -> None:
        lines = [
            Spoken(MIA, 0, 4.0, "a", GAME),
            Spoken(DEE, 5_000, 3.0, "b", OFF_TOPIC),
            Spoken(MIA, 9_000, 3.0, "c", GAME),
        ]
        once = lean_to_keeping(lines)
        self.assertEqual(lean_to_keeping(once), once)
        self.assertLess(3.0, KEEP_STRETCH_S)

    def test_a_marker_says_where_its_lines_are(self) -> None:
        lines = [
            Spoken(DEE, 1_000, 5.0, "a", OFF_TOPIC),
            Spoken(DEE, 7_000, 5.0, "b", OFF_TOPIC),
        ]
        (item,) = collapse(lines)
        self.assertEqual((item.started_ms, item.until_ms, item.count), (1_000, 7_000, 2))


if __name__ == "__main__":
    unittest.main()
