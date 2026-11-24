"""dev1's name-pass tool with `--reader` (#5163): the name reader's spellings join the pass's
candidates as a live session's do with DMBOT_SESSION_NAMES on (`DMBot._read_before_pass`), from
exactly the lines the pass reads, with the same names to skip and why. Pure: a stand-in reader,
no database, no AI. Invented names."""

from __future__ import annotations

import asyncio
import contextlib
import io
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.devtools.name_pass.__main__ import parse_args, reader_lines, reader_spellings
from dmbot.memory import name_reader
from dmbot.memory.scan import Suggestion


def line(user: int, text: str, topic: str = "game", sidebar: str = "", typed: bool = False) -> Any:
    return SimpleNamespace(user_id=user, text=text, topic=topic, sidebar=sidebar, typed=typed)


class TheReaderSpellings(unittest.TestCase):
    def run_it(self, stored: list[Any]) -> tuple[Any, dict[str, Any]]:
        seen: dict[str, Any] = {}

        async def read(rows: Any, skip: Any, **kw: Any) -> Any:
            seen.update(rows=list(rows), skip=dict(skip), **kw)
            heard = [name_reader.Heard("vorna", "person", name_reader.SPOKEN_OF, ("1", "2"))]
            return name_reader.Read(heard=heard, unread=1)

        got = asyncio.run(
            reader_spellings(
                read,
                stored,
                known={"kethrandil", "glyph"},
                rejected={"glyph"},
                people={"sam"},
                dms={9},
                rules=("2024", "2014"),
                consenting=lambda: None,
                max_tokens=16000,
            )
        )
        return got, seen

    def test_the_reader_numbers_its_lines_and_marks_the_dm(self) -> None:
        stored = [line(9, "We met vorna."), line(1, "Then vorna left.")]
        (_, _), seen = self.run_it(stored)
        rows = seen["rows"]
        self.assertEqual(
            [(r.number, r.text, r.dm) for r in rows],
            [("1", "We met vorna.", True), ("2", "Then vorna left.", False)],
        )

    def test_a_live_session_s_lines_are_its_heard_lines(self) -> None:
        # (spoken and typed, never the sidebar or what the filter left out; only who agrees)
        stored = [
            line(1, "Spoken."), line(1, "Typed.", typed=True), line(1, "Aside.", sidebar="q1"),
            line(1, "Lunch?", topic="off_topic"), line(1, "Rules talk.", topic="table_talk"),
            line(2, "Not agreed."),
        ]  # fmt: skip
        got = [x.text for x in reader_lines(stored, {1}, game_only=False)]
        self.assertEqual(got, ["Spoken.", "Typed.", "Rules talk."])

    def test_an_import_s_lines_are_its_game_talk(self) -> None:
        stored = [line(1, "Spoken."), line(1, "Rules talk.", topic="table_talk")]
        self.assertEqual([x.text for x in reader_lines(stored, {1}, game_only=True)], ["Spoken."])

    def test_the_names_to_skip_carry_why_as_the_bot_s_do(self) -> None:
        (_, _), seen = self.run_it([line(1, "We met vorna.")])
        self.assertEqual(
            seen["skip"],
            {
                "kethrandil": name_reader.KNOWN_NAME,
                "glyph": name_reader.NOT_A_NAME,
                "sam": name_reader.AT_TABLE,
            },
        )
        self.assertEqual(seen["known"], ["kethrandil"])
        self.assertEqual((seen["rules"], seen["max_tokens"]), (("2024", "2014"), 16000))

    def test_each_name_kept_is_a_candidate_with_its_hearings(self) -> None:
        stored = [line(1, "We met vorna."), line(1, "Then vorna left.")]
        (spellings, unread), _ = self.run_it(stored)
        self.assertEqual(spellings, [Suggestion("vorna", 2)])
        self.assertEqual(unread, 1)

    def test_with_no_line_to_read_the_reader_is_not_called(self) -> None:
        (spellings, unread), seen = self.run_it([])
        self.assertEqual((spellings, unread, seen), ([], 0, {}))

    def test_the_flag(self) -> None:
        args = parse_args(["--guild", "1", "--session", "abc", "--reader", "--yes"])
        self.assertEqual((args.reader, args.yes), (True, True))
        self.assertFalse(parse_args(["--guild", "1", "--session", "abc"]).reader)

    def test_the_reader_without_yes_is_refused_before_anything_runs(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            parse_args(["--guild", "1", "--session", "abc", "--reader"])
        self.assertEqual(stop.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
