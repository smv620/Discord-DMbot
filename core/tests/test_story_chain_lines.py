"""What the walk of where the story is may read, and its backup section's checks (#4173 slice 4;
the reviewer's cases): the lines the story extractor reads, last time's recap left out, and only
people who agree now, the DM included; a damaged backup row refuses the section. Pure."""

from __future__ import annotations

import unittest

from dmbot.campaigns.models import CampaignError
from dmbot.memory.story_chain_backup import ROWS_MAX, _checked
from dmbot.memory.story_chain_store import readable_lines
from dmbot.transcript.models import Line
from dmbot.transcript.topics import OFF_TOPIC

DM, P1, P2 = 7, 8, 9


def line(ms: int, who: int, text: str, **kw: object) -> Line:
    return Line(started_ms=ms, user_id=who, heard=text, text=text, **kw)  # type: ignore[arg-type]


class WhatIsRead(unittest.TestCase):
    def test_only_agreed_lines_play_lines_and_no_recap(self) -> None:
        lines = [
            line(1, DM, "Last time, you reached Targos."),  # (line 1: the recap read aloud)
            line(2, DM, "You walk into the Gull's Rest."),
            line(3, P1, "I order a drink."),
            line(4, P2, "I look around."),  # (P2 doesn't agree)
            line(5, DM, "Is that rule right?", sidebar="question"),  # (the DM's sidebar)
            line(6, P1, "What's for dinner tonight?", topic=OFF_TOPIC),
        ]
        got = readable_lines(lines, recap={1}, agreed={DM, P1})
        self.assertEqual([s.started_ms for s in got], [2, 3])

    def test_a_dm_who_stopped_recording_is_not_read(self) -> None:
        got = readable_lines([line(1, DM, "You reach Targos.")], recap=set(), agreed={P1})
        self.assertEqual(got, [])


ROW = {"chain": "main", "rank": 0, "entity_id": "a" * 32, "updated_at": 5}
MARK = {"chain": "mark", "rank": 0, "entity_id": None, "updated_at": 5}
TOWN = {"chain": "town", "rank": 0, "entity_id": "b" * 32, "updated_at": 5}  # (#4338)


class TheBackupChecks(unittest.TestCase):
    def test_good_rows_and_the_mark(self) -> None:
        self.assertEqual(len(_checked([ROW, MARK, TOWN])), 3)

    def test_every_damaged_row_refuses_the_section(self) -> None:
        bad = [
            {**ROW, "extra": 1},
            {k: v for k, v in ROW.items() if k != "rank"},
            {**ROW, "chain": "s1g4"},
            {**ROW, "rank": 21},
            {**ROW, "rank": True},
            {**ROW, "entity_id": "not-an-id"},
            {**ROW, "entity_id": None},  # (only the mark has no place)
            {**MARK, "entity_id": "a" * 32},
            {**MARK, "rank": 1},
            {**TOWN, "rank": 1},  # (one last town)
            {**TOWN, "entity_id": None},  # (the town is a place)
            {**ROW, "updated_at": 0},
        ]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(CampaignError):
                _checked([row])
        with self.assertRaises(CampaignError):
            _checked([ROW, ROW])  # (one place per rank)
        with self.assertRaises(CampaignError):
            _checked([{**ROW, "rank": n % 21} for n in range(ROWS_MAX + 1)])
        with self.assertRaises(CampaignError):
            _checked("not a list")
