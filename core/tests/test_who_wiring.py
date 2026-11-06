"""The bot's "who is this" step (#3602, slice 2d): off means off, and when it runs it asks inside
the extractor's limits, with the DM's ranking (hidden entries may be a choice) and the people who
are recorded now. `DMBot.write_who` on a stand-in with the parts canned; the step's own rules are
in `test_who_step`. Invented names."""

from __future__ import annotations

import unittest
import unittest.mock
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.memory import who_step
from dmbot.memory.models import PROPOSED, Entity
from dmbot.transcript.store import Around

DM = 7
WAITING, KNOWN = "d" * 32, "f" * 32  # (not the ids of `test_who_step`'s choices)


def entity(eid: str, name: str, status: str) -> Entity:
    return Entity(eid, "character", name, "", status, None, "dm", 0)


class Wiring(unittest.IsolatedAsyncioTestCase):
    def fake(self, **settings: Any) -> Any:
        on = {"story_who_on": True, "story_extractor_on": True, **settings}
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(started_at=9)),
            extraction=AsyncMock(return_value=(0, 0)),
            extractor_calls_since=AsyncMock(return_value=0),
            record_extraction=AsyncMock(),
            around=AsyncMock(return_value=Around(None, (8, "Vale waves at the table."), None)),
        )
        memory = SimpleNamespace(
            entities=AsyncMock(return_value=[entity(WAITING, "Vale", PROPOSED)]),
            mentions=AsyncMock(return_value=[("s:1000:8", 1)]),
            lookup_data=AsyncMock(side_effect=AssertionError("not read when nothing waits")),
        )
        return SimpleNamespace(
            settings=SimpleNamespace(
                story_extractor_max_calls=480, story_extractor_max_calls_day=960, **on
            ),
            extractor_ai=SimpleNamespace(model="m"),
            memory=memory,
            transcripts=transcripts,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c"))),
            plan_allows=AsyncMock(return_value=True),
            consent=SimpleNamespace(consenting=AsyncMock(return_value=frozenset({8}))),
        )

    def table(self) -> Any:
        return SimpleNamespace(
            campaign_id="c", transcript_session_id="s", guild_id=1, dm_user_id=DM,
            dm_user_ids=frozenset(),
        )  # fmt: skip

    async def test_off_means_off_nothing_is_read_or_asked(self) -> None:
        for settings in ({"story_who_on": False}, {"story_extractor_on": False}):
            me = self.fake(**settings)
            with patch.object(who_step, "run", AsyncMock()) as run:
                await DMBot.write_who(me, self.table())
            self.assertFalse(run.await_count)
            self.assertFalse(me.memory.entities.await_count)
            self.assertFalse(me.campaigns.get.await_count)
            self.assertFalse(me.consent.consenting.await_count)
            self.assertFalse(me.memory.lookup_data.await_count)
            self.assertFalse(me.transcripts.around.await_count)

    async def test_a_campaign_whose_plan_does_not_allow_it_is_left_alone(self) -> None:
        me = self.fake()
        me.plan_allows = AsyncMock(return_value=False)
        await DMBot.write_who(me, self.table())
        self.assertFalse(me.memory.entities.await_count)

    async def test_nothing_waiting_means_no_call_and_no_lookup_read(self) -> None:
        me = self.fake()
        me.memory.entities = AsyncMock(return_value=[entity(KNOWN, "Mira", "confirmed")])
        with patch("dmbot.bot.WhoAnswers") as store:
            store.return_value.sweep = AsyncMock()
            store.return_value.missing = AsyncMock(return_value=[])
            with patch.object(who_step, "run", AsyncMock()) as run:
                await DMBot.write_who(me, self.table())
        self.assertFalse(run.await_count)

    async def test_a_name_with_nothing_to_offer_asks_nothing(self) -> None:
        me = self.fake()
        me.memory.lookup_data = AsyncMock(return_value=None)
        with (
            patch("dmbot.bot.WhoAnswers") as store,
            patch("dmbot.bot.CampaignLookup") as lookup,
            patch.object(who_step, "candidates_from", lambda *a, **k: []),
            patch.object(who_step, "run", AsyncMock()) as run,
        ):
            lookup.build = lambda data: object()
            store.return_value.sweep = AsyncMock()
            store.return_value.missing = AsyncMock(return_value=[WAITING])
            await DMBot.write_who(me, self.table())
        self.assertFalse(run.await_count)  # (nobody to ask about: no call, and no lines read)
        self.assertFalse(me.transcripts.around.await_count)


class Asking(Wiring):
    """The step with a lookup that holds real entries to offer (the same invented campaign as
    `test_who_step`)."""

    def fake(self, **settings: Any) -> Any:
        from tests.test_who_step import lookup

        me = super().fake(**settings)
        data = lookup()
        me.memory.lookup_data = AsyncMock(return_value=data)
        me.lookup = data
        return me

    step_run: Any
    answers_cls: Any

    def patches(self, run_report: who_step.Report | None = None) -> Any:
        from contextlib import ExitStack

        from tests.test_who_step import lookup

        stack = ExitStack()
        self.answers_cls = stack.enter_context(patch("dmbot.bot.WhoAnswers"))
        self.answers_cls.return_value.sweep = AsyncMock()
        self.answers_cls.return_value.missing = AsyncMock(side_effect=lambda g, c, ids: list(ids))
        stack.enter_context(
            patch("dmbot.bot.CampaignLookup", SimpleNamespace(build=lambda data: lookup()))
        )
        self.step_run = stack.enter_context(
            patch.object(who_step, "run", AsyncMock(return_value=run_report or who_step.Report()))
        )
        return stack

    async def test_a_name_with_something_to_offer_is_built_with_its_speakers_and_asked(
        self,
    ) -> None:
        from tests.test_who_step import MIRA

        me = self.fake()
        with self.patches():
            await DMBot.write_who(me, self.table())
        self.step_run.assert_awaited_once()
        (pending,) = self.step_run.await_args.args[2]
        self.assertEqual(pending.item.speaker_ids, (8,))  # (the speakers lines_for returned)
        self.assertIn("Player: Vale waves at the table.", pending.item.lines)
        self.assertIn(MIRA, pending.refs.values())
        kw = self.step_run.await_args.kwargs
        self.assertEqual((kw["max_calls"], kw["guild_id"], kw["campaign_id"]), (480, 1, "c"))
        await kw["count_call"]()
        me.transcripts.record_extraction.assert_awaited_once()
        self.assertEqual(me.transcripts.record_extraction.await_args.kwargs["calls"], 1)
        me.plan_allows.assert_awaited_with("story", 1, unittest.mock.ANY, fail_open=False)

    async def test_no_call_left_reads_nothing_more(self) -> None:
        me = self.fake()
        me.transcripts.extraction = AsyncMock(return_value=(0, 480))  # (the session's calls used)
        with self.patches():
            await DMBot.write_who(me, self.table())
        self.assertFalse(me.memory.lookup_data.await_count)
        self.assertFalse(self.step_run.await_count)

    async def test_names_with_nothing_to_offer_never_fill_the_cap_and_their_lines_are_not_read(
        self,
    ) -> None:
        me = self.fake()
        nobody = [entity(f"{n:032x}", f"Zorb{n}", PROPOSED) for n in range(1, 126)]
        asked = [entity(f"{n:032x}", "Vale", PROPOSED) for n in range(900, 905)]
        me.memory.entities = AsyncMock(return_value=[*nobody, *asked])
        with self.patches():
            await DMBot.write_who(me, self.table())
        (pending_list,) = self.step_run.await_args.args[2:3]
        self.assertEqual(len(pending_list), 5)  # (the last five were reached)
        self.assertEqual(me.transcripts.around.await_count, 5)  # (only theirs were read)

    async def test_the_cap_counts_names_asked_about(self) -> None:
        me = self.fake()
        many = [entity(f"{n:032x}", "Vale", PROPOSED) for n in range(1, 131)]
        me.memory.entities = AsyncMock(return_value=many)
        with self.patches():
            await DMBot.write_who(me, self.table())
        self.assertEqual(len(self.step_run.await_args.args[2]), 120)

    async def test_a_name_said_in_a_session_but_with_no_readable_line_is_skipped(self) -> None:
        me = self.fake()
        me.transcripts.around = AsyncMock(return_value=None)  # (the speaker stopped)
        with self.patches():
            await DMBot.write_who(me, self.table())
        self.assertFalse(self.step_run.await_count)

    async def test_a_name_from_the_dms_own_document_has_no_pointers_and_is_still_asked(
        self,
    ) -> None:
        me = self.fake()
        me.memory.mentions = AsyncMock(return_value=[])
        with self.patches():
            await DMBot.write_who(me, self.table())
        (pending,) = self.step_run.await_args.args[2]
        self.assertEqual(pending.item.speaker_ids, ())


class Lines(unittest.IsolatedAsyncioTestCase):
    async def test_only_lines_of_people_who_agree_are_read_and_their_speakers_returned(
        self,
    ) -> None:
        around = AsyncMock(
            side_effect=[
                Around((9, "before"), (8, "Vale waves."), None),
                None,  # (a line that is gone, or whose speaker stopped)
            ]
        )
        transcripts: Any = SimpleNamespace(around=around)
        mentions = AsyncMock(return_value=[("s:1000:8", 1), ("s:2000:9", 1), ("bad", 1)])
        lines, speakers = await who_step.lines_for(
            WAITING,
            mentions,
            transcripts,
            1,
            {DM},
            {8, 9},
        )
        self.assertEqual(lines, ("Player: before", "Player: Vale waves."))
        self.assertEqual(speakers, (8, 9))
        for call in around.await_args_list:
            self.assertEqual(call.args[4], {8, 9})  # (only the people who agree now)

    async def test_the_dms_lines_say_so(self) -> None:
        transcripts: Any = SimpleNamespace(
            around=AsyncMock(return_value=Around(None, (DM, "Vale"), None))
        )
        mentions = AsyncMock(return_value=[("s:1000:7", 1)])
        lines, _ = await who_step.lines_for(
            WAITING,
            mentions,
            transcripts,
            1,
            {DM},
            {DM},
        )
        self.assertEqual(lines, ("DM: Vale",))

    def test_a_pointer_is_read_back_into_its_parts(self) -> None:
        self.assertEqual(who_step.parse_ref("sess:1500:9"), ("sess", 1500, 9))
        self.assertIsNone(who_step.parse_ref("nonsense"))
        self.assertIsNone(who_step.parse_ref("a:b:c"))
