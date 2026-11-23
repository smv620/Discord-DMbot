"""The per-name trace of the after-session name step (#5055 slice B): written for a sample
campaign only, read back, and used by why_missed for a name never stored. Invented names."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dmbot.devtools import sample_name
from dmbot.memory import name_trace as nt

SAMPLE = sample_name.name_for(41, 2)


class Writing(unittest.TestCase):
    def test_a_sample_campaign_is_traced_and_read_back(self) -> None:
        rows = [
            nt.Row(nt.READER, "hrothgar", "too_few_hearings"),
            nt.Row(nt.SCAN, "Targos", nt.OFFERED, times=3),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = nt.write(d, SAMPLE, "c1", "s1", rows)
            assert path is not None
            self.assertEqual(path.name, "trace-c1.jsonl")
            nt.write(d, SAMPLE, "c1", "s2", [nt.Row(nt.READER, "Mira", nt.OVER_CAP)])
            back = nt.read(path)
        self.assertEqual([r.name for r in back], ["hrothgar", "Targos", "Mira"])
        self.assertEqual(back[1].times, 3)
        self.assertEqual(
            dict(nt.counts(back)),
            {
                (nt.READER, "too_few_hearings"): 1,
                (nt.SCAN, nt.OFFERED): 1,
                (nt.READER, nt.OVER_CAP): 1,
            },
        )

    def test_a_real_campaign_or_no_folder_is_never_traced(self) -> None:
        rows = [nt.Row(nt.SCAN, "Targos", nt.OFFERED)]
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(nt.write(d, "Rime of the Frostmaiden", "c1", "s1", rows))
            self.assertEqual(list(Path(d).iterdir()), [])
        self.assertIsNone(nt.write("", SAMPLE, "c1", "s1", rows))

    def test_a_folder_that_cannot_be_written_never_raises(self) -> None:
        self.assertIsNone(nt.write("/nonexistent/folder", SAMPLE, "c1", None, []))

    def test_a_broken_line_is_passed_over(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "trace-c1.jsonl")
            path.write_text('not json\n{"source": "scan", "name": "Targos", "reason": "offered"}\n')
            self.assertEqual([r.name for r in nt.read(path)], ["Targos"])

    def test_rows_group_by_name_key(self) -> None:
        rows = [
            nt.Row(nt.SCAN, "Bryn Shander", nt.OVER_CAP),
            nt.Row(nt.READER, "bryn shander", nt.OFFERED),
        ]
        self.assertEqual(len(nt.by_key(rows)["bryn shander"]), 2)


class WhyMissed(unittest.TestCase):
    def test_a_name_never_stored_says_what_the_trace_says(self) -> None:
        from dmbot.devtools import why_missed as wm

        trace = nt.by_key([
            nt.Row(nt.SCAN, "Thornwatch", nt.OVER_CAP),
            nt.Row(nt.READER, "thornwatch", "too_few_hearings"),
        ])  # fmt: skip
        self.assertEqual(
            wm._not_stored_why(["thornwatch"], trace),
            "never stored: reader ended it as too_few_hearings",
        )
        self.assertEqual(wm._not_stored_why(["mira"], trace), "never stored: no reader listed it")
        self.assertEqual(
            wm._not_stored_why(["mira"], None), "never stored (no name trace for this run)"
        )


class TheBotStep(unittest.TestCase):
    def test_each_name_ends_offered_over_the_number_or_refused_from_its_source(self) -> None:
        import asyncio
        from types import SimpleNamespace
        from typing import Any

        from dmbot.bot import DMBot
        from dmbot.memory.scan import Suggestion

        with tempfile.TemporaryDirectory() as d:

            async def get(*_: Any) -> Any:
                return SimpleNamespace(name=SAMPLE)

            bot: Any = SimpleNamespace(
                campaigns=SimpleNamespace(get=get), settings=SimpleNamespace(name_trace_dir=d)
            )
            every = [Suggestion("Targos", 4), Suggestion("hrothgar", 2), Suggestion("Vell", 2)]
            found = [*every[:2], Suggestion("Brin", 1)]  # (Vell is past the number; Brin asked)
            earlier = [nt.Row(nt.READER, "mira", "too_few_hearings")]
            asyncio.run(
                DMBot._write_name_trace(
                    bot, 5, "c1", "s1", every, found,
                    {"targos", "hrothgar", "brin"}, {"hrothgar"}, {"targos", "vell"}, earlier,
                    {"hrothgar"},
                )
            )  # fmt: skip
            rows = nt.read(Path(d, "trace-c1.jsonl"))
        self.assertEqual(
            [(r.source, r.name, r.reason) for r in rows],
            [
                (nt.READER, "mira", "too_few_hearings"),
                (nt.SCAN, "Targos", nt.OFFERED),
                (nt.READER, "hrothgar", nt.STORE_REFUSED),
                (nt.SCAN, "Vell", nt.OVER_CAP),
                (nt.ASKED, "Brin", nt.OFFERED),
            ],
        )
