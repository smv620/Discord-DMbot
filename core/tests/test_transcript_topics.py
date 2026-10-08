"""The off-topic filter's pure parts (#52), without Discord, a database or AI."""

import unittest

from dmbot.transcript.topics import (
    GAME,
    OFF_TOPIC,
    TABLE_TALK,
    Spoken,
    collapse,
    marker,
    obviously_game,
)

MIA, DEE = 8, 9


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


if __name__ == "__main__":
    unittest.main()
