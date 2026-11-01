"""What a point of view believed at a game time (#3091, part 3): beliefs with no game time are never
in it, the latest at or before the time wins, ties break by seq, and a player gets only their own
characters' views through the gate. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import MemoryRuleError
from tests import test_lies as lies_t
from tests.test_memory_store import GUILD_A

DAY = 1440


class AsOf(lies_t.Lies):
    async def at(self, view: str, game_time: int, **kw: Any) -> Any:
        return await self.memory.believed_at(GUILD_A, self.c, view, game_time, source="dm", **kw)

    async def held(self, view: str, confidence: float, game_time: int | None) -> Any:
        return await self.think(view, "theory", confidence, [], game_time=game_time)

    async def test_the_latest_belief_at_or_before_the_time_is_the_answer(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.3, 10 * DAY)
        await self.held(view, 0.6, 20 * DAY)
        await self.held(view, 0.9, 30 * DAY)
        for when, expected in ((10 * DAY, 0.3), (15 * DAY, 0.3), (20 * DAY, 0.6), (99 * DAY, 0.9)):
            with self.subTest(when):
                (got,) = await self.at(view, when)
                self.assertEqual((got.confidence, got.relation_id), (expected, self.rel))
        self.assertEqual(await self.at(view, 9 * DAY), [])  # (before anything was believed)

    async def test_a_belief_with_no_game_time_never_counts(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.3, None)
        await self.held(view, 0.5, None)
        self.assertEqual(await self.at(view, 10**6), [])  # (ask the session-order reader instead)
        await self.held(view, 0.7, 5 * DAY)
        await self.held(view, 0.8, None)  # newer, but with no time: not the answer
        (got,) = await self.at(view, 10**6)
        self.assertEqual(got.confidence, 0.7)

    async def test_one_timeline_a_later_record_of_an_earlier_time_does_not_win(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.9, 30 * DAY)
        await self.held(view, 0.2, 10 * DAY)  # recorded later, about earlier (a flashback)
        (got,) = await self.at(view, 40 * DAY)
        self.assertEqual(got.confidence, 0.9)
        (earlier,) = await self.at(view, 15 * DAY)
        self.assertEqual(earlier.confidence, 0.2)

    async def test_ties_at_the_same_minute_break_by_seq(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.3, 10 * DAY)
        await self.held(view, 0.8, 10 * DAY)
        (got,) = await self.at(view, 10 * DAY)
        self.assertEqual(got.confidence, 0.8)  # the one recorded last

    async def test_each_point_of_view_is_its_own(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.3, 10 * DAY)
        await self.held(self.truth, 0.9, 10 * DAY)
        (mine,) = await self.at(view, 10 * DAY)
        self.assertEqual((mine.perspective_id, mine.entity_id), (view, self.pc))

    async def test_only_the_dm_asks_for_any_view(self) -> None:
        view = await self.pc_view()
        with self.assertRaises(MemoryRuleError):
            await self.memory.believed_at(GUILD_A, self.c, view, 1, source="extractor")

    async def test_a_player_sees_their_own_characters_views_and_no_others(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.4, 10 * DAY)
        await self.held(self.truth, 0.9, 10 * DAY)
        mine = await self.memory.my_characters_believed_at(GUILD_A, self.c, 1, 12 * DAY)
        self.assertEqual([(b.entity_id, b.confidence) for b in mine], [(self.pc, 0.4)])
        other_player = 999  # plays no character here
        self.assertEqual(
            await self.memory.my_characters_believed_at(GUILD_A, self.c, other_player, 12 * DAY), []
        )

    async def test_a_connection_the_player_may_not_know_is_not_in_it(self) -> None:
        view = await self.pc_view()
        await self.held(view, 0.4, 10 * DAY)
        await self.memory.set_connection_knowers(GUILD_A, self.c, self.rel, [], source="dm")
        self.assertEqual(
            await self.memory.my_characters_believed_at(GUILD_A, self.c, 1, 12 * DAY), []
        )
