"""The lore read's per-name trace (#5106): every name a lore read lists ends with one fixed reason
(listed, dropped by a rule, folded, stored), collected only when a caller is collecting, written
only for a sample campaign on the test server, and read by why_missed. Invented names; no AI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import AIError, Reply
from dmbot.devtools import sample_name, why_missed
from dmbot.memory import background as bg
from dmbot.memory import lore_read as lr
from dmbot.memory import name_reader
from dmbot.memory import name_trace as nt
from dmbot.memory import trace_note as tn
from tests.test_lore_read import LIST, NAME_ROWS, TEXT, Gate, ai

SAMPLE = sample_name.name_for(41, 1)


def found(name: str, kind: str = "npc", **kw: Any) -> bg.Found:
    return bg.Found(name, kind, tuple(kw.get("also", ())), tuple(kw.get("secret", ())),
                    kw.get("quote", ""))  # fmt: skip


class TheCollector(unittest.TestCase):
    def test_a_note_is_kept_only_while_collecting_and_in_order(self) -> None:
        tn.note("Mira", tn.LISTED)  # (nobody is collecting: nothing happens)
        with tn.collecting() as rows:
            tn.note("Mira", tn.LISTED)
            tn.note("Aldric", tn.FOLDED, "Brother Aldric")
            tn.note("", tn.LISTED)  # (no name: nothing to trace)
        tn.note("Mira", tn.STORED)
        self.assertEqual(
            rows,
            [(tn.LORE, "Mira", tn.LISTED, ""), (tn.LORE, "Aldric", tn.FOLDED, "Brother Aldric")],
        )

    def test_a_picture_read_s_notes_say_so_and_two_reads_never_share_a_list(self) -> None:
        async def read_one(name: str, picture: bool) -> list[tn.Note]:
            with tn.collecting() as rows:
                if picture:
                    with tn.from_source(tn.LORE_PICTURE):
                        await _note_later(name)
                else:
                    await _note_later(name)
            return rows

        async def both() -> list[list[tn.Note]]:
            return list(await asyncio.gather(read_one("Mira", False), read_one("Tavin", True)))

        first, second = asyncio.run(both())
        self.assertEqual(first, [(tn.LORE, "Mira", tn.STORED, "")])
        self.assertEqual(second, [(tn.LORE_PICTURE, "Tavin", tn.STORED, "")])

    def test_the_read_s_own_tasks_write_to_the_same_list(self) -> None:
        async def run() -> list[tn.Note]:
            with tn.collecting() as rows:
                await asyncio.gather(
                    asyncio.ensure_future(_note_later("A")), asyncio.create_task(_note_later("B"))
                )
            return rows

        self.assertEqual(sorted(n for _s, n, _r, _w in asyncio.run(run())), ["A", "B"])


async def _note_later(name: str) -> None:
    await asyncio.sleep(0)
    tn.note(name, tn.STORED)


class PassTwosChecks(unittest.TestCase):
    def test_each_refused_name_has_its_reason(self) -> None:
        piece = "Mira met them at Greyfen Abbey."
        reply = json.dumps(
            {
                "names": [
                    {"name": "Mira", "kind": "person"},
                    {"name": "Vellos", "kind": "person"},  # (not in the text)
                    {"name": "them", "kind": "person"},  # (a pronoun the text doesn't cue)
                    {"name": " ".join(["Word"] * 12), "kind": "person"},  # (a sentence)
                ]
            }
        )
        with tn.collecting() as rows:
            kept = bg.read_answer(reply, piece)
        self.assertEqual([f.name for f in kept], ["Mira"])
        self.assertEqual(
            {(n, r) for _s, n, r, _w in rows},
            {
                ("Vellos", tn.NOT_IN_TEXT),
                ("them", tn.ORDINARY),
                (" ".join(["Word"] * 12), tn.TOO_LONG),
            },
        )


class TheMerge(unittest.TestCase):
    def test_known_common_rules_generic_folded_over_and_split_names(self) -> None:
        names = [
            found("Mira"),
            found("Oskar Vane"),  # (the campaign has it)
            found("Yes"),  # (an ordinary word)
            found("Goblin"),  # (a monster the rules hold)
            found("dagger", "item"),  # (not one particular thing)
            found("Vecnor", secret=["Brother Aldric"]),
            found("Brother Aldric"),  # (Vecnor's disguise: folds into him)
            found("Tavin, Lio and Mira"),  # (a list: split into its names)
            found("Lio"),
            found("Tavin"),
        ]
        with tn.collecting() as rows:
            kept = bg.merge([names], ["Oskar Vane"], cap=3, rules=("2024", "2014"))
        self.assertEqual(len(kept), 3)
        ends = {n: (r, w) for _s, n, r, w in rows}
        self.assertEqual(ends["Oskar Vane"], (tn.KNOWN, ""))
        self.assertEqual(ends["Yes"], (tn.COMMON, ""))
        self.assertEqual(ends["Goblin"], (tn.RULES, ""))
        self.assertEqual(ends["dagger"], (tn.ITEM, ""))
        self.assertEqual(ends["Brother Aldric"], (tn.FOLDED, "Vecnor"))
        self.assertEqual(ends["Tavin, Lio and Mira"], (tn.SPLIT, ""))
        over = [n for n, (r, _w) in ends.items() if r == tn.OVER_CAP]
        self.assertEqual(len(over), 1)  # (four kept names, a limit of three)

    def test_passes_says_why_a_held_name_is_refused(self) -> None:
        with tn.collecting() as rows:
            for f in (found("Oskar Vane"), found("Goblin"), found("dagger", "item")):
                bg.passes(f, ["Oskar Vane"], rules=("2024", "2014"))
        self.assertEqual(
            [(n, r) for _s, n, r, _w in rows],
            [("Oskar Vane", tn.KNOWN), ("Goblin", tn.RULES), ("dagger", tn.ITEM)],
        )

    def test_fold_names_says_which_name_kept_it(self) -> None:
        with tn.collecting() as rows:
            out = lr.fold_names([found("Mira", also=["the Fox"]), found("the Fox")])
        self.assertEqual([f.name for f in out], ["Mira"])
        self.assertEqual(rows, [(tn.LORE, "the Fox", tn.FOLDED, "Mira")])


class TheWholeRead(unittest.TestCase):
    def test_every_listed_name_is_traced_and_a_known_one_says_so(self) -> None:
        async def run() -> list[tn.Note]:
            with tn.collecting() as rows:
                await lr.read_lore(
                    ai([Reply(LIST, False)]), ai([]), TEXT, {}, Gate(),
                    skip_name=lambda name: name == "Greyfen Abbey",
                )  # fmt: skip
            return rows

        rows = asyncio.run(run())
        listed = [n for _s, n, r, _w in rows if r == tn.LISTED]
        self.assertEqual(listed, [r["name"] for r in NAME_ROWS])
        self.assertIn((tn.LORE, "Greyfen Abbey", tn.KNOWN, ""), rows)

    def test_a_read_stopped_mid_shaping_ends_its_slices_at_the_time_limit(self) -> None:
        started = asyncio.Event()

        async def hang(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("never answered")

        async def run() -> list[tn.Note]:
            with tn.collecting() as rows:
                read = asyncio.ensure_future(
                    lr.read_lore(
                        ai([Reply(LIST, False)]),
                        SimpleNamespace(complete=AsyncMock(side_effect=hang), model="m"),
                        TEXT,
                        {},
                        Gate(),
                    )
                )
                await started.wait()
                read.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read
            return rows

        stopped = {n for _s, n, r, _w in asyncio.run(run()) if r == tn.TIME_LIMIT}
        self.assertEqual(stopped, {r["name"] for r in NAME_ROWS})

    def test_an_unreadable_answer_or_an_unknown_kind_is_not_silent(self) -> None:
        async def garbled(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            return Reply("not json at all", False)

        async def run(shaper: Any) -> list[tn.Note]:
            with tn.collecting() as rows:
                await lr.read_lore(ai([Reply(LIST, False)]), shaper, TEXT, {}, Gate())
            return rows

        rows = asyncio.run(run(SimpleNamespace(complete=AsyncMock(side_effect=garbled), model="m")))
        self.assertEqual(
            {n for _s, n, r, _w in rows if r == tn.SLICE_UNREAD}, {r["name"] for r in NAME_ROWS}
        )
        with tn.collecting() as kinds:
            bg.read_answer(json.dumps({"names": [{"name": "Mira", "kind": "dragon"}]}), "Mira")
        self.assertEqual(kinds, [(tn.LORE, "Mira", tn.UNKNOWN_KIND, "")])

    def test_a_slice_that_failed_says_its_names_went_unread(self) -> None:
        async def fail(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            raise AIError("busy")

        async def run() -> list[tn.Note]:
            with tn.collecting() as rows:
                await lr.read_lore(
                    ai([Reply(LIST, False)]),
                    SimpleNamespace(complete=AsyncMock(side_effect=fail), model="m"),
                    TEXT, {}, Gate(),
                )  # fmt: skip
            return rows

        rows = asyncio.run(run())
        unread = {n for _s, n, r, _w in rows if r == tn.SLICE_UNREAD}
        self.assertEqual(unread, {r["name"] for r in NAME_ROWS})


class TheWrite(unittest.TestCase):
    def bot(self, folder: str, rejected: set[str]) -> Any:
        async def keys(*_: Any) -> set[str]:
            return rejected

        return SimpleNamespace(
            settings=SimpleNamespace(name_trace_dir=folder, name_trace_guild=5),
            memory=SimpleNamespace(rejected_keys=keys),
        )

    def test_a_sample_campaign_s_rows_are_written_with_the_dm_s_not_a_name_apart(self) -> None:
        from dmbot.ui import name_background as nb

        with tempfile.TemporaryDirectory() as d:
            campaign: Any = SimpleNamespace(guild_id=5, id="c1", name=SAMPLE)
            bot = self.bot(d, {"hope"})
            self.assertTrue(nb._tracing(bot, campaign))
            noted = [
                (tn.LORE, "Mira", tn.LISTED, ""),
                (tn.LORE, "Hope", tn.KNOWN, ""),
                (tn.LORE_PICTURE, "Oskar", tn.KNOWN, ""),
            ]
            asyncio.run(nb._write_lore_trace(bot, campaign, noted))
            rows = nt.read(Path(d, "trace-c1.jsonl"))
        self.assertEqual(
            [(r.source, r.name, r.reason) for r in rows],
            [
                (nt.LORE, "Mira", tn.LISTED),
                (nt.LORE, "Hope", name_reader.NOT_A_NAME),
                (tn.LORE_PICTURE, "Oskar", tn.KNOWN),
            ],
        )

    def test_a_read_that_stopped_ends_what_was_only_listed(self) -> None:
        from dmbot.ui import name_background as nb

        with tempfile.TemporaryDirectory() as d:
            campaign: Any = SimpleNamespace(guild_id=5, id="c1", name=SAMPLE)
            noted = [
                (tn.LORE, "Mira", tn.LISTED, ""),
                (tn.LORE, "Tavin", tn.LISTED, ""),
                (tn.LORE, "Tavin", tn.STORED, ""),
            ]
            asyncio.run(nb._write_lore_trace(self.bot(d, set()), campaign, noted, stopped=True))
            rows = nt.read(Path(d, "trace-c1.jsonl"))
        self.assertEqual([(r.name, r.reason) for r in rows][-1], ("Mira", tn.READ_STOPPED))
        self.assertEqual(sum(r.reason == tn.READ_STOPPED for r in rows), 1)

    def test_a_real_campaign_or_another_server_is_never_traced(self) -> None:
        from dmbot.ui import name_background as nb

        with tempfile.TemporaryDirectory() as d:
            bot = self.bot(d, set())
            self.assertFalse(nb._tracing(bot, SimpleNamespace(guild_id=5, id="c", name="Rime")))
            self.assertFalse(nb._tracing(bot, SimpleNamespace(guild_id=6, id="c", name=SAMPLE)))
            self.assertFalse(
                nb._tracing(self.bot("", set()), SimpleNamespace(guild_id=5, id="c", name=SAMPLE))
            )


class WhyMissed(unittest.TestCase):
    def why(self, keys: list[str], rows: list[nt.Row]) -> str:
        return why_missed._not_stored_why(keys, why_missed._trace_index(rows))

    def test_a_lore_name_says_which_step_ended_it(self) -> None:
        rows = [
            nt.Row(nt.LORE, "azure tome", tn.LISTED),
            nt.Row(nt.LORE, "azure tome", tn.ITEM),
            nt.Row(nt.LORE, "the Underdark", tn.LISTED),
            nt.Row(nt.LORE, "Aldric", tn.LISTED),
            nt.Row(nt.LORE, "Aldric", tn.FOLDED, "Brother Aldric"),
        ]
        self.assertEqual(
            self.why(["azure tome"], rows), "never stored: lore ended it as item_not_unique"
        )
        self.assertEqual(
            self.why(["underdark"], rows),
            "never stored: the lore read listed it in pass 1, but pass 2 gave no line for it",
        )
        self.assertEqual(
            self.why(["aldric"], rows),
            "never stored: lore ended it as folded_into_another (Brother Aldric)",
        )
        self.assertEqual(self.why(["gemstone mine"], rows), "never stored: no reader listed it")

    def test_a_later_read_listing_it_again_never_hides_how_it_ended(self) -> None:
        rows = [
            nt.Row(nt.LORE, "Auril", tn.LISTED),
            nt.Row(nt.LORE, "Auril", tn.NOT_IN_TEXT),
            nt.Row(tn.LORE_PICTURE, "Auril", tn.LISTED),  # (the pictures' read, afterwards)
        ]
        self.assertEqual(self.why(["auril"], rows), "never stored: lore ended it as not_in_text")


class TheStore(unittest.TestCase):
    def test_a_stored_name_and_a_refused_one_each_end_so(self) -> None:
        from dmbot.ui import name_background as nb

        async def add_names(*_: Any, **__: Any) -> Any:
            return SimpleNamespace(value=["e1", None])  # (the second's key was taken meanwhile)

        memory: Any = SimpleNamespace(add_names=add_names)
        campaign: Any = SimpleNamespace(guild_id=5, id="c1", name=SAMPLE)

        async def run() -> list[tn.Note]:
            with tn.collecting() as rows:
                await nb.store(memory, campaign, [found("Mira"), found("Tavin")])
            return rows

        self.assertEqual(
            [(n, r) for _s, n, r, _w in asyncio.run(run())],
            [("Mira", tn.STORED), ("Tavin", tn.STORE_REFUSED)],
        )
