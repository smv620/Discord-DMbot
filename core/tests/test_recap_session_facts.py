"""A recap says which session it is (#2418): the number the transcripts show and how long it ran.
Pure: no database, so it always runs."""

from __future__ import annotations

import unittest

from dmbot.web.recaps import session_facts

# As the transcripts page's door lists them: newest first, numbered by start time.
SESSIONS = [
    {"number": 3, "startedAt": 3000, "endedAt": None},  # still listening
    {"number": 2, "startedAt": 2000, "endedAt": 2000 + 2 * 3600 + 35 * 60},
    {"number": 1, "startedAt": 1000, "endedAt": 1000 + 45 * 60},
]


class TheSessionFacts(unittest.TestCase):
    def test_the_number_is_the_transcripts_number_and_the_length_is_end_minus_start(self) -> None:
        self.assertEqual(session_facts(SESSIONS, 2000), (2, 9300))
        self.assertEqual(session_facts(SESSIONS, 1000), (1, 2700))

    def test_a_session_still_listening_has_a_number_and_no_length(self) -> None:
        self.assertEqual(session_facts(SESSIONS, 3000), (3, None))

    def test_a_recap_with_no_session_has_neither(self) -> None:
        self.assertEqual(session_facts(SESSIONS, 999), (None, None))
        self.assertEqual(session_facts([], 1000), (None, None))

    def test_an_end_before_the_start_is_never_negative(self) -> None:
        self.assertEqual(session_facts([{"number": 1, "startedAt": 50, "endedAt": 40}], 50), (1, 0))

    def test_a_session_matches_by_start_time_not_by_position(self) -> None:
        # Two campaigns' sessions are never mixed: the door is asked once for each campaign, so a
        # list holds only that campaign's sessions, and the start time picks the one.
        mixed = [{"number": 7, "startedAt": 100, "endedAt": 160}]
        self.assertEqual(session_facts(mixed, 100), (7, 60))
        self.assertEqual(session_facts(mixed, 101), (None, None))


if __name__ == "__main__":
    unittest.main()
