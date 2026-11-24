"""dev1's name-pass tool with `--reader` (#5163): the name reader's spellings join the pass's
candidates as a live session's do with DMBOT_SESSION_NAMES on (`DMBot._read_before_pass`), from
exactly the lines the pass reads, with the same names to skip and why. Pure: a stand-in reader,
no database, no AI. Invented names."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.devtools.name_pass.__main__ import parse_args, reader_spellings
from dmbot.memory import name_reader
from dmbot.memory.scan import Suggestion


def line(user: int, text: str) -> Any:
    return SimpleNamespace(user_id=user, text=text)


class TheReaderSpellings(unittest.TestCase):
    def run_it(self, stored: list[Any], pass_texts: list[str]) -> tuple[Any, dict[str, Any]]:
        seen: dict[str, Any] = {}

        async def read(rows: Any, skip: Any, **kw: Any) -> Any:
            seen.update(rows=list(rows), skip=dict(skip), **kw)
            heard = [name_reader.Heard("vorna", "person", name_reader.SPOKEN_OF, ("1", "2"))]
            return name_reader.Read(heard=heard, unread=1)

        got = asyncio.run(
            reader_spellings(
                read,
                stored,
                pass_texts,
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

    def test_the_reader_reads_only_the_lines_the_pass_reads(self) -> None:
        stored = [line(9, "We met vorna."), line(1, "A sidebar line."), line(1, "Then vorna left.")]
        (_, _), seen = self.run_it(stored, ["We met vorna.", "Then vorna left."])
        rows = seen["rows"]
        self.assertEqual([(r.number, r.text) for r in rows],
                         [("1", "We met vorna."), ("2", "Then vorna left.")])  # fmt: skip
        self.assertEqual([r.dm for r in rows], [True, False])

    def test_the_names_to_skip_carry_why_as_the_bot_s_do(self) -> None:
        (_, _), seen = self.run_it([line(1, "We met vorna.")], ["We met vorna."])
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
        (spellings, unread), _ = self.run_it(stored, ["We met vorna.", "Then vorna left."])
        self.assertEqual(spellings, [Suggestion("vorna", 2)])
        self.assertEqual(unread, 1)

    def test_with_no_line_to_read_the_reader_is_not_called(self) -> None:
        (spellings, unread), seen = self.run_it([line(1, "Off topic.")], [])
        self.assertEqual((spellings, unread, seen), ([], 0, {}))

    def test_the_flag(self) -> None:
        args = parse_args(["--guild", "1", "--session", "abc", "--reader", "--yes"])
        self.assertEqual((args.reader, args.yes), (True, True))
        self.assertFalse(parse_args(["--guild", "1", "--session", "abc"]).reader)


if __name__ == "__main__":
    unittest.main()
