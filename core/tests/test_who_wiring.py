"""The bot's "who is this" step (#3602, slice 2d): off means off, and when it runs it asks inside
the extractor's limits, with the DM's ranking (hidden entries may be a choice) and the people who
are recorded now. `DMBot.write_who` on a stand-in with the parts canned; the step's own rules are
in `test_who_step`. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.memory import who_step
from dmbot.memory.models import PROPOSED, Entity
from dmbot.transcript.store import Around

DM = 7
WAITING, KNOWN = "a" * 32, "b" * 32


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

    async def test_a_waiting_name_is_asked_inside_the_limits_with_the_dms_ranking(self) -> None:
        me = self.fake()
        me.memory.lookup_data = AsyncMock(return_value=None)
        calls: dict[str, Any] = {}

        def fake_candidates(lookup: Any, *, include_hidden: bool, leave_out: Any = ()) -> list[Any]:
            calls["hidden"], calls["leave_out"] = include_hidden, list(leave_out)
            return []

        with (
            patch("dmbot.bot.WhoAnswers") as store,
            patch("dmbot.bot.CampaignLookup") as lookup,
            patch.object(who_step, "candidates_from", fake_candidates),
            patch.object(who_step, "run", AsyncMock(return_value=who_step.Report())) as run,
        ):
            lookup.build = lambda data: object()
            store.return_value.sweep = AsyncMock()
            store.return_value.missing = AsyncMock(return_value=[WAITING])
            await DMBot.write_who(me, self.table())
        self.assertEqual(calls, {"hidden": True, "leave_out": [WAITING]})
        # nothing could be offered here (no candidates), so nobody was asked
        run.assert_awaited_once()
        self.assertEqual(run.await_args.args[2], [])  # type: ignore[union-attr]
        self.assertEqual(run.await_args.kwargs["max_calls"], 480)  # type: ignore[union-attr]


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
