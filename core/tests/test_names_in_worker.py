"""The name step of a live session's end in the worker (#4905 slice 4c): the lines the live path
would have held are rebuilt from the stored transcript and give the same name pass the same
arguments; consent is asked afresh when the block starts and the pass is marked done whichever way
it ends; with the switch off, or no worker, the step is exactly the bot's own; the bot waits for the
worker and reads back ids only. Stand-ins, no database."""

from __future__ import annotations

import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dmbot import bot as botmod
from dmbot.bot import DMBot
from dmbot.config import ConfigError, load_settings
from dmbot.memory.models import PROPOSED
from dmbot.transcript import after_session_jobs as jobs
from dmbot.transcript.held_lines import HEARD_MAX, held_lines
from dmbot.transcript.models import SIDEBAR_ANSWER, Line
from tests.test_after_session_worker import _Jobs, _table
from tests.test_ai_watch import BASE

SID = "s" * 8
CID = "c" * 32
DM, OTHER_DM = 7, 3


def line(user: int, text: str, **kw: Any) -> Line:
    return Line(1000, user, text, text, 0, **kw)


class TheLinesTheScanReads(unittest.TestCase):
    def test_spoken_and_typed_lines_of_people_who_agree_are_kept_in_order(self) -> None:
        got = held_lines([line(1, "a"), line(2, "b"), line(1, "c", typed=True)], {1, 2})
        self.assertEqual(got, [(1, "a"), (2, "b"), (1, "c")])

    def test_a_speaker_who_stopped_is_left_out_by_who_said_it(self) -> None:
        self.assertEqual(held_lines([line(1, "a"), line(2, "a")], {1}), [(1, "a")])

    def test_the_sidebar_pictures_off_topic_and_empty_lines_are_left_out(
        self,
    ) -> None:
        lines = [
            line(1, "keep"),
            line(DM, "question", sidebar=SIDEBAR_ANSWER),
            line(1, "a map", typed=True, image=True),
            line(1, "hidden", topic="off_topic"),
            line(1, "   "),
            line(1, "table", topic="table_talk"),  # (only off-topic is hidden)
        ]
        self.assertEqual(held_lines(lines, {1, DM}), [(1, "keep"), (1, "table")])

    def test_a_typed_line_the_filter_called_off_topic_is_still_scanned_as_it_is_live(self) -> None:
        # (the live path scans every typed line; the label only hides it from the transcript)
        lines = [line(1, "Gorvash, lol pizza", typed=True, topic="off_topic")]
        self.assertEqual(held_lines(lines, {1}), [(1, "Gorvash, lol pizza")])

    def test_it_stops_at_the_bots_own_cap(self) -> None:
        many = [line(1, f"w{n}") for n in range(HEARD_MAX + 5)]
        self.assertEqual(len(held_lines(many, {1})), HEARD_MAX)
        self.assertEqual(len(held_lines(many, {1}, limit=3)), 3)


def _me(*, worker_on: bool, names_on: bool, queue: _Jobs | None = None) -> Any:
    me: Any = SimpleNamespace(
        settings=SimpleNamespace(worker_on=worker_on, worker_names_on=names_on),
        after_jobs=queue,
        headless=False,
        memory=SimpleNamespace(entity=mock.AsyncMock(), entities=mock.AsyncMock(return_value=[])),
        transcripts=SimpleNamespace(
            lines=mock.AsyncMock(return_value=[]), mark_names_repaired=mock.AsyncMock()
        ),
        consent=SimpleNamespace(consenting=mock.AsyncMock(return_value=frozenset({1, 2}))),
        save_transcript=mock.AsyncMock(),
        name_calls=[],
        lookup=SimpleNamespace(drop=mock.Mock()),
        tables={},
    )
    for name in (
        "_worker_does_after_session",
        "_worker_does_names",
        "_hand_names_to_worker",
        "_hand_to_worker",
        "_found_names",
        "_after_lease",
        "_after_card",
        "_worker_name_pass",
    ):
        setattr(me, name, partial(getattr(DMBot, name), me))

    async def local(table: Any) -> None:  # the bot's own step, as a stand-in
        me.name_calls.append("bot's own")
        table.heard = []
        table.new_names = ["Local"]

    me._suggest_names = local
    me.post_message = mock.AsyncMock(return_value=None)
    return me


def _bot_with(queue: _Jobs) -> Any:
    return _me(worker_on=True, names_on=True, queue=queue)


def _session() -> Any:
    table = _table()
    table.heard = [(1, "hello")]
    table.new_names = []
    return table


class WhoDoesIt(unittest.IsolatedAsyncioTestCase):
    async def test_with_the_names_switch_off_the_bot_runs_its_own_step(self) -> None:
        me = _me(worker_on=True, names_on=False, queue=_Jobs())
        await DMBot.suggest_names(me, _session())
        self.assertEqual(me.name_calls, ["bot's own"])
        self.assertEqual(me.after_jobs.added, [])

    async def test_the_names_switch_alone_does_nothing_without_the_worker(self) -> None:
        me = _me(worker_on=False, names_on=True, queue=_Jobs())
        await DMBot.suggest_names(me, _session())
        self.assertEqual(me.name_calls, ["bot's own"])

    async def test_with_both_on_it_is_handed_over_and_the_bot_reads_back_ids_only(self) -> None:
        queue = _Jobs()
        me = _me(worker_on=True, names_on=True, queue=queue)
        queue.results = {jobs.NAMES: ["e" * 32, "f" * 32]}
        me.memory.entity = mock.AsyncMock(
            side_effect=[
                SimpleNamespace(name="Oskar Vane", status=PROPOSED),
                SimpleNamespace(name="Gone", status="rejected"),
            ]
        )

        async def worker_finishes(_: float) -> None:
            queue.rows[jobs.NAMES] = jobs.DONE

        table = _session()
        with mock.patch("asyncio.sleep", worker_finishes):
            await DMBot.suggest_names(me, table)
        self.assertEqual(queue.added, [(jobs.NAMES, (3, 7))])
        self.assertEqual(me.name_calls, [])  # (the bot did not run its own)
        self.assertEqual(table.new_names, ["Oskar Vane"])  # (only a name still waiting)
        self.assertEqual(table.heard, [])
        me.lookup.drop.assert_called_once_with([CID])  # (the bot's copy lacks the new names)
        me.save_transcript.assert_awaited()  # (the lines are stored before the worker reads them)
        me.transcripts.mark_names_repaired.assert_awaited()  # (the pass is marked done)

    async def test_a_block_nobody_takes_is_run_by_the_bot_once_and_its_names_stand(self) -> None:
        queue = _Jobs()
        me = _me(worker_on=True, names_on=True, queue=queue)
        table = _session()
        with mock.patch.object(botmod, "WORKER_TAKE_S", 0):
            await DMBot.suggest_names(me, table)
        self.assertEqual(me.name_calls, ["bot's own"])
        self.assertEqual(table.new_names, ["Local"])  # (not overwritten by the worker's answer)

    async def test_a_bot_that_gives_up_waiting_leaves_the_mark_to_the_worker(self) -> None:
        queue = _Jobs()
        me = _bot_with(queue)
        table = _session()

        async def tick(_: float) -> None:
            return None

        # (the worker took it and is still running: the recap must not start on half-repaired lines)
        await queue.add(1, CID, SID, jobs.NAMES, {3})
        queue.rows[jobs.NAMES], queue.holder[jobs.NAMES] = jobs.RUNNING, "worker-live"
        with (
            mock.patch.object(botmod, "WORKER_TAKE_S", 10**9),
            mock.patch.object(botmod, "WORKER_WAIT_MAX_S", 0),
            mock.patch("asyncio.sleep", tick),
            self.assertLogs("dmbot.bot", level="WARNING"),
        ):
            await DMBot.suggest_names(me, table)
        me.transcripts.mark_names_repaired.assert_not_awaited()
        self.assertEqual(table.new_names, [])
        self.assertEqual(me.name_calls, [])

    async def test_five_database_errors_in_a_row_also_leave_the_mark_alone(self) -> None:
        class Broken(_Jobs):
            async def status(self, *a: Any) -> jobs.Status | None:
                raise RuntimeError("the database")

        me = _bot_with(Broken())

        async def tick(_: float) -> None:
            return None

        with (
            mock.patch.object(botmod, "WORKER_TAKE_S", 10**9),
            mock.patch("asyncio.sleep", tick),
            self.assertLogs("dmbot.bot", level="WARNING"),
        ):
            await DMBot.suggest_names(me, _session())
        me.transcripts.mark_names_repaired.assert_not_awaited()

    async def test_a_worker_that_failed_still_lets_the_pass_be_marked_done(self) -> None:
        queue = _Jobs()
        queue.rows[jobs.NAMES] = jobs.FAILED
        me = _bot_with(queue)
        with self.assertLogs("dmbot.bot", level="WARNING"):
            await DMBot.suggest_names(me, _session())
        me.transcripts.mark_names_repaired.assert_awaited()

    async def test_a_session_with_no_stored_transcript_is_the_bots_own_step(self) -> None:
        me = _me(worker_on=True, names_on=True, queue=_Jobs())
        table = _session()
        table.transcript_session_id = None
        await DMBot.suggest_names(me, table)
        self.assertEqual(me.name_calls, ["bot's own"])


class TheWorkersPass(unittest.IsolatedAsyncioTestCase):
    def stored(self) -> list[Line]:
        return [
            line(1, "hello"),
            line(2, "the Ferry Lady"),
            line(1, "typed", typed=True),
            line(DM, "ask", sidebar=SIDEBAR_ANSWER),
            line(1, "off", topic="off_topic"),
            line(4, "stopped"),
        ]

    async def test_the_worker_gives_the_name_pass_what_the_bots_own_step_gives_it(self) -> None:
        # the live path held these (the sidebar and off-topic lines never reach `table.heard`)
        table = _session()
        table.heard = [(1, "hello"), (2, "the Ferry Lady"), (1, "typed"), (4, "stopped")]
        bots = _me(worker_on=True, names_on=True)
        bots.consent.consenting = mock.AsyncMock(return_value=frozenset({1, 2, DM, OTHER_DM}))
        bots.save_transcript = mock.AsyncMock()
        bots._name_pass = mock.AsyncMock(return_value=[])
        bots._after_card = lambda t: None
        with mock.patch.object(DMBot, "_name_pass", bots._name_pass):
            await DMBot._suggest_names(bots, table)

        worker = _me(worker_on=True, names_on=True)
        worker.consent.consenting = mock.AsyncMock(return_value=frozenset({1, 2, DM, OTHER_DM}))
        worker.transcripts.lines = mock.AsyncMock(return_value=self.stored())
        worker._name_pass = mock.AsyncMock(return_value=[])
        with mock.patch.object(DMBot, "_name_pass", worker._name_pass):
            await DMBot._worker_name_pass(worker, 1, CID, SID, {DM, OTHER_DM}, [])

        (bot_call,) = bots._name_pass.call_args_list
        (worker_call,) = worker._name_pass.call_args_list
        # (who agrees now, the lines with their speakers in order, and the people whose names
        # are not story names: the same, value for value; only the card differs)
        self.assertEqual(bot_call.args[4:7], worker_call.args[4:7])
        self.assertEqual(bot_call.args[4], [(1, "hello"), (2, "the Ferry Lady"), (1, "typed")])
        self.assertEqual(bot_call.args[5], {1, 2, 4, DM, OTHER_DM})

    async def test_consent_is_asked_afresh_as_the_block_starts_and_a_stopped_speaker_is_out(
        self,
    ) -> None:
        worker = _me(worker_on=True, names_on=True)
        order: list[str] = []

        async def consenting(_g: int) -> frozenset[int]:
            order.append("consent")
            return frozenset({1})  # (speaker 2 stopped since the session ended)

        async def lines(_g: int, _s: str) -> list[Line]:
            order.append("lines")
            return self.stored()

        worker.consent.consenting = consenting
        worker.transcripts.lines = lines
        worker._name_pass = mock.AsyncMock(return_value=[])
        with mock.patch.object(DMBot, "_name_pass", worker._name_pass):
            await DMBot._worker_name_pass(worker, 1, CID, SID, {DM}, [])
        self.assertEqual(order[:2], ["consent", "lines"])  # (before anything is read)
        heard = worker._name_pass.call_args.args[4]
        self.assertEqual({who for who, _ in heard}, {1})

    async def test_the_pass_is_marked_done_even_when_it_fails_or_finds_nothing(self) -> None:
        worker = _me(worker_on=True, names_on=True)
        worker.transcripts.lines = mock.AsyncMock(return_value=[])
        await DMBot._worker_name_pass(worker, 1, CID, SID, {DM}, [])
        worker.transcripts.mark_names_repaired.assert_awaited_once()

        broken = _me(worker_on=True, names_on=True)
        broken.transcripts.lines = mock.AsyncMock(return_value=self.stored())
        broken._name_pass = mock.AsyncMock(side_effect=RuntimeError("down"))
        with (
            mock.patch.object(DMBot, "_name_pass", broken._name_pass),
            self.assertRaises(RuntimeError),
        ):
            await DMBot._worker_name_pass(broken, 1, CID, SID, {DM}, [])
        broken.transcripts.mark_names_repaired.assert_awaited_once()

    async def test_what_it_proposed_comes_back_as_ids_of_the_names_still_waiting(self) -> None:
        worker = _me(worker_on=True, names_on=True)
        worker.transcripts.lines = mock.AsyncMock(return_value=self.stored())
        worker._name_pass = mock.AsyncMock(return_value=["Oskar Vane"])
        worker.memory.entities = mock.AsyncMock(
            return_value=[
                SimpleNamespace(id="a" * 32, name="Oskar Vane"),
                SimpleNamespace(id="b" * 32, name="Someone Else"),
            ]
        )
        found: list[str] = []
        with mock.patch.object(DMBot, "_name_pass", worker._name_pass):
            await DMBot._worker_name_pass(worker, 1, CID, SID, {DM}, found)
        self.assertEqual(found, ["a" * 32])

    async def test_the_names_block_runs_the_pass_and_hands_back_its_ids(self) -> None:
        me: Any = SimpleNamespace(
            consent=SimpleNamespace(consenting=mock.AsyncMock(return_value=frozenset())), calls=[]
        )

        async def worker_pass(g: int, c: str, s: str, dm: Any, found: list[str]) -> None:
            me.calls.append((g, c, s, set(dm)))
            found.append("a" * 32)

        me._worker_name_pass = worker_pass
        ids: list[str] = []
        ok = await DMBot._run_after_block(me, 1, CID, SID, {DM}, jobs.NAMES, None, ids)
        self.assertTrue(ok)
        self.assertEqual(me.calls, [(1, CID, SID, {DM})])
        self.assertEqual(ids, ["a" * 32])


class TheSetting(unittest.TestCase):
    def test_the_names_switch_needs_the_worker_and_is_off_by_default(self) -> None:
        self.assertFalse(load_settings(BASE).worker_names_on)
        self.assertFalse(load_settings({**BASE, "DMBOT_WORKER_NAMES": "1"}).worker_names_on)
        both = {**BASE, "DMBOT_WORKER": "1", "DMBOT_WORKER_NAMES": "1"}
        self.assertTrue(load_settings(both).worker_names_on)

    def test_a_bad_value_stops_start_up(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DMBOT_WORKER_NAMES": "maybe"})
