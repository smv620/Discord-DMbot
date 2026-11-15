"""A time limit told to the DM when the clock reaches it (quests slice 4b; design §8.4; Supervisor's
ruling on #4448): "soon" and "time's up" once each for each part row, apart from the change log;
a clock jump straight past says only "time's up"; a moved date is told afresh; a finished thread
says nothing; every clock-moving path checks (CI reads the source). The store's part needs
Postgres. Invented names."""

from __future__ import annotations

import ast
import pathlib
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import thread_parts as tp
from dmbot.memory import time_limits as tl
from dmbot.memory.models import DM
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import MINUTES_PER_DAY as DAY
from dmbot.ui import time_limit_alerts as alerts
from tests.test_memory_store import GUILD_A, MemoryTest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "dmbot"
# ClockStore's calls that move a campaign's clock (or bring back a group that may be ahead).
# `change_named` is the clock's alone; the others are common names (the names list has its own
# `undo_split`), so they count only when called on a clock store.
ONLY_CLOCKS = {"change_named"}
ON_A_CLOCK = {"change", "rejoin", "undo_split", "undo_rejoin"}


def _moves(fn: ast.AST) -> bool:
    """Whether a function moves a clock, even with the call passed on as a value (as the split
    Undo does)."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            if node.attr in ONLY_CLOCKS:
                return True
            if node.attr in ON_A_CLOCK and "clock" in ast.unparse(node.value):
                return True
    return False


class EveryClockMove(unittest.TestCase):
    """Supervisor's condition on #4448: every function that moves the clock checks the limits."""

    def test_every_function_that_moves_the_clock_checks_the_time_limits(self) -> None:
        missing, found = [], 0
        for path in SRC.rglob("*.py"):
            if path.parent.name == "timebot":
                continue  # (the clock's own store and pure parts: they move nothing by themselves)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _moves(fn) or any(
                    isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and _moves(inner)
                    for inner in fn.body
                ):
                    continue  # (an outer function is judged by its inner one)
                found += 1
                calls = {
                    c.func.attr
                    for c in ast.walk(fn)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                }
                if "after_clock" not in calls:
                    missing.append(f"{path.relative_to(SRC)}:{fn.name}")
        self.assertEqual(missing, [])
        self.assertGreaterEqual(found, 5)  # (press, run_rejoin, both Undos, the split Undo)


class TheMessage(unittest.TestCase):
    def test_soon_and_times_up_in_the_cards_words(self) -> None:
        soon = alerts.message(
            "Hlin's job", "before the new moon", "1 day left (due Day 4)", tl.SOON
        )
        self.assertEqual(soon, "⏰ **Hlin's job**: 1 day left (due Day 4).\n> before the new moon")
        up = alerts.message("Hlin's job", "x", "time's up (was due Day 4)", tl.PASSED)
        self.assertIn(alerts.PASSED_TAIL, up)
        self.assertNotIn("⏳", soon + up)  # (⏰ for time limits; ⏳ is "gone quiet")

    def test_words_cant_open_markdown(self) -> None:
        said = alerts.message("**Hlin**", "[x](y)", "1 day left (due Day 4)", tl.SOON)
        self.assertIn("\\*\\*Hlin\\*\\*", said)
        self.assertIn("\\[x\\]\\(y\\)", said)


class Telling(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, "Hlin's job", "quest", "active", aim="", parts=[], source=DM
        )
        self.quest = kept.value.id
        added = await self.memory.add_thread_part(
            GUILD_A, self.c, self.quest, tp.TIME_LIMIT, "before the new moon", source=DM
        )
        dated = await self.memory.set_time_limit_date(
            GUILD_A, self.c, added.value.id, 5 * DAY, now=0, source=DM
        )
        self.limit = dated.value.id

    async def tell(self, now: int) -> list[str]:
        got = await self.memory.time_limits_to_tell(GUILD_A, self.c, now, at=1)
        return [stage for _, _, _, stage in got]

    async def test_soon_then_times_up_once_each(self) -> None:
        self.assertEqual(await self.tell(3 * DAY), [])
        self.assertEqual(await self.tell(4 * DAY), [tl.SOON])
        self.assertEqual(await self.tell(4 * DAY + 60), [])  # (said once)
        self.assertEqual(await self.tell(5 * DAY), [tl.PASSED])
        self.assertEqual(await self.tell(9 * DAY), [])
        self.assertEqual(await self.tell(4 * DAY), [])  # (the clock set back: nothing again)

    async def test_a_jump_past_it_says_only_times_up(self) -> None:
        self.assertEqual(await self.tell(8 * DAY), [tl.PASSED])
        self.assertEqual(await self.tell(4 * DAY), [])  # ("soon" never comes after it)

    async def test_a_moved_date_is_told_afresh(self) -> None:
        self.assertEqual(await self.tell(5 * DAY), [tl.PASSED])
        await self.memory.set_time_limit_date(
            GUILD_A, self.c, self.limit, 10 * DAY, now=5 * DAY, source=DM
        )
        self.assertEqual(await self.tell(9 * DAY), [tl.SOON])

    async def test_a_limit_made_hard_or_ticked_and_unticked_is_not_told_again(self) -> None:
        self.assertEqual(await self.tell(5 * DAY), [tl.PASSED])
        hard = await self.memory.correct_thread_part(
            GUILD_A, self.c, self.limit, source=DM, limit_kind=tp.HARD
        )
        self.assertNotEqual(hard.value.id, self.limit)  # (a story change: a new row)
        self.assertEqual(await self.tell(6 * DAY), [])
        met = await self.memory.set_thread_part_state(
            GUILD_A, self.c, hard.value.id, "met", source=DM
        )
        await self.memory.set_thread_part_state(GUILD_A, self.c, met.value.id, "running", source=DM)
        self.assertEqual(await self.tell(7 * DAY), [])

    async def test_two_moves_at_once_tell_it_once(self) -> None:
        import asyncio

        both = await asyncio.gather(self.tell(4 * DAY), self.tell(4 * DAY))
        self.assertEqual(sorted(stage for told in both for stage in told), [tl.SOON])

    async def test_a_suggested_limit_not_kept_is_never_told(self) -> None:
        # (a read DMbot suggested and the DM hasn't kept: status 'read', never the DM's own)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_thread_parts SET status = 'read' WHERE id = %s", [self.limit]
            )
        self.assertEqual(await self.tell(9 * DAY), [])

    async def test_a_finished_thread_says_nothing(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.quest, "resolved", source=DM)
        self.assertEqual(await self.tell(9 * DAY), [])

    async def test_after_clock_tells_each_dm_and_never_raises(self) -> None:
        sent: list[tuple[int, str]] = []

        async def get(*_: Any) -> Any:
            return SimpleNamespace(clock=SimpleNamespace(minute=4 * DAY), groups=())

        async def calendar(*_: Any) -> Calendar:
            return Calendar()

        async def campaign(*_: Any) -> Any:
            return SimpleNamespace(dm_user_ids=frozenset({11, 22}))

        async def dm_message(user_id: int, text: str, view: Any) -> Any:
            sent.append((user_id, text))
            return object()

        client = SimpleNamespace(
            memory=self.memory,
            clocks=SimpleNamespace(get=get, calendar=calendar),
            campaigns=SimpleNamespace(get=campaign),
            _dm_message=dm_message,
        )
        self.assertEqual(await alerts.after_clock(client, GUILD_A, self.c), 2)
        self.assertEqual([u for u, _ in sent], [11, 22])
        self.assertIn("1 day left (due Day 6)", sent[0][1])
        self.assertEqual(await alerts.after_clock(client, GUILD_A, self.c), 0)  # (once)

        async def closed(user_id: int, text: str, view: Any) -> Any:
            return None  # (private messages closed: claimed anyway, never repeated)

        client._dm_message = closed
        self.assertEqual(await alerts.after_clock(client, GUILD_A, self.c), 0)

        async def broken(*_: Any) -> Any:
            raise RuntimeError("down")

        client.memory = SimpleNamespace(time_limits_to_tell=broken)
        client.clocks = SimpleNamespace(get=get, calendar=calendar)
        self.assertEqual(await alerts.after_clock(client, GUILD_A, self.c), 0)


if __name__ == "__main__":
    unittest.main()
