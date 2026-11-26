"""The weather note comes once per place and kind (#4748; Supervisor's ruling on #4747): the mark
says a note was sent, judged against which kept climate row and value; a new kept row or an edited
value lets it come again, and the mark goes with the place or the climate row. Invented names.
Needs Postgres."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import weather_marks as wm
from dmbot.memory.models import CONFIRMED, NewName, new_id
from tests.test_memory_names import DM, GUILD, NamesTest


class OncePerPlace(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Sunreach", "place", CONFIRMED)], source="dm"
        )
        (place,) = made.value
        assert place is not None
        self.place: str = place
        self.climate = await self.climate_row("hot")

    async def climate_row(
        self, value: str, *, place: str | None = None, campaign: str | None = None
    ) -> str:
        row = new_id()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, created_at) VALUES (%s, %s, %s, %s, 'climate', %s, 'confirmed',"
                " 'dm', 1)",
                (GUILD, campaign or self.campaign.id, row, place or self.place, value),
            )
        return row

    async def first(self, *marks: wm.Mark) -> list[bool]:
        return await wm.first_time(self.db, GUILD, self.campaign.id, marks, now=5)

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT kind, climate_id, climate FROM memory_weather_notes ORDER BY kind"
            )
            return [(r["kind"], r["climate_id"], r["climate"]) for r in await cur.fetchall()]

    async def test_a_second_sessions_same_weather_gives_no_second_note(self) -> None:
        snow = wm.Mark(self.place, "snow", self.climate, "hot")
        self.assertEqual(await self.first(snow, wm.Mark(self.place, "frost", self.climate, "hot")),
                         [True, True])  # fmt: skip
        self.assertEqual(await self.first(snow), [False])
        self.assertEqual(
            await self.rows(), [("frost", self.climate, "hot"), ("snow", self.climate, "hot")]
        )

    async def test_a_changed_climate_lets_it_come_again(self) -> None:
        await self.first(wm.Mark(self.place, "snow", self.climate, "hot"))
        # (the DM edits the kept row's value: the same row, another climate)
        self.assertEqual(
            await self.first(wm.Mark(self.place, "snow", self.climate, "warm")), [True]
        )
        # (a new kept row in its place, the old one turned down: another id)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_map_facts SET status = 'rejected' WHERE id = %s", (self.climate,)
            )
        other = await self.climate_row("hot")
        self.assertEqual(await self.first(wm.Mark(self.place, "snow", other, "hot")), [True])
        self.assertEqual(await self.rows(), [("snow", other, "hot")])

    async def test_it_goes_with_its_climate_row_and_its_place(self) -> None:
        await self.first(wm.Mark(self.place, "snow", self.climate, "hot"))
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM memory_map_facts WHERE id = %s", (self.climate,))
        self.assertEqual(await self.rows(), [])
        again = await self.climate_row("hot")
        await self.first(wm.Mark(self.place, "snow", again, "hot"))
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM memory_map_facts WHERE entity_id = %s", (self.place,))
            await conn.execute("DELETE FROM memory_entities WHERE id = %s", (self.place,))
        self.assertEqual(await self.rows(), [])

    async def test_a_second_kept_row_with_the_same_value_is_no_change(self) -> None:
        # (which kept row is chosen never decides it: the marks take them all)
        await self.first(wm.Mark(self.place, "snow", self.climate, "hot", (self.climate,)))
        other = new_id()  # (as if a second kept row, the region's, were judged among them)
        again = wm.Mark(self.place, "snow", self.climate, "hot", (other, self.climate))
        self.assertEqual(await self.first(again), [False])

    async def test_a_regions_climate_is_judged_against_for_the_place(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("the Sun Coast", "place", CONFIRMED)], source="dm"
        )
        (region,) = made.value
        assert region is not None
        row = new_id()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, created_at) VALUES (%s, %s, %s, %s, 'climate', 'hot',"
                " 'confirmed', 'dm', 1)",
                (GUILD, self.campaign.id, row, region),
            )
        mark = wm.Mark(self.place, "heat", row, "hot", (row,))
        self.assertEqual(await self.first(mark), [True])
        self.assertEqual(await self.first(mark), [False])

    async def test_one_place_heard_under_two_names_in_one_read_is_one_note(self) -> None:
        mark = wm.Mark(self.place, "snow", self.climate, "hot", (self.climate,))
        self.assertEqual(await self.first(mark, mark), [True, False])

    async def test_a_place_gone_is_not_new(self) -> None:
        self.assertEqual(await self.first(wm.Mark("x" * 32, "snow", self.climate, "hot")), [False])
        self.assertEqual(await self.rows(), [])


class ForTheWorkersRow(OncePerPlace):
    """The outbox row of #4904 3b-4b holds a moment and a session date; the bot reads the notes
    back from the marks made at that moment and says what the read says itself."""

    async def test_the_marks_made_at_a_moment_are_read_back_by_it_alone(self) -> None:
        await wm.first_time(
            self.db, GUILD, self.campaign.id,
            [wm.Mark(self.place, "snow", self.climate, "hot")], now=40,
        )  # fmt: skip
        await wm.first_time(
            self.db, GUILD, self.campaign.id,
            [wm.Mark(self.place, "frost", self.climate, "hot")], now=90,
        )  # fmt: skip
        got = await wm.noted_at(self.db, GUILD, self.campaign.id, 40)
        self.assertEqual(got, [(self.place, "snow", "hot")])
        self.assertEqual(await wm.noted_at(self.db, GUILD, self.campaign.id, 41), [])

    async def test_delivery_says_the_note_privately_as_the_read_does(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot
        from dmbot.maps import weather_note
        from dmbot.ui.thread_questions import session_label

        await wm.first_time(
            self.db, GUILD, self.campaign.id,
            [wm.Mark(self.place, "snow", self.climate, "hot")], now=40,
        )  # fmt: skip
        bot: Any = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(dm_accounts=frozenset({7})))
            ),
            memory=self.memory,
            _dm_message=AsyncMock(return_value=object()),
            tables={},
        )
        row = {"at": 40, "session": 1_000}
        await DMBot.deliver_story_weather(bot, GUILD, self.campaign.id, row)
        text = bot._dm_message.await_args.args[1]
        want = weather_note.message(
            [weather_note.Note("Sunreach", "snow", "hot", None, None)], session_label(1_000)
        )
        self.assertEqual((bot._dm_message.await_args.args[0], text), (7, want))
        bot._dm_message = AsyncMock(return_value=None)  # a refused message is tried again
        with self.assertRaises(RuntimeError):
            await DMBot.deliver_story_weather(bot, GUILD, self.campaign.id, row)
        self.assertEqual(
            bot._dm_message.await_count, 1
        )  # (and nothing more is said for a moment with no marks)
        bot._dm_message.reset_mock()
        await DMBot.deliver_story_weather(bot, GUILD, self.campaign.id, {**row, "at": 41})
        bot._dm_message.assert_not_awaited()


class WhileTheTableIsPlaying(OncePerPlace):
    async def test_a_row_that_arrives_during_play_sends_nothing_and_marks_nothing(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        await wm.first_time(
            self.db, GUILD, self.campaign.id,
            [wm.Mark(self.place, "snow", self.climate, "hot")], now=40,
        )  # fmt: skip
        bot: Any = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(dm_accounts=frozenset({7})))
            ),
            memory=self.memory,
            _dm_message=AsyncMock(return_value=object()),
            tables={GUILD: SimpleNamespace(campaign_id=self.campaign.id)},  # (the table is live)
        )
        row = {"at": 40, "session": 1_000}
        await DMBot.deliver_story_weather(bot, GUILD, self.campaign.id, row)
        bot._dm_message.assert_not_awaited()
        self.assertEqual(await wm.noted_at(self.db, GUILD, self.campaign.id, 40), [])
        # (so a later read may note it: the mark is new again)
        self.assertEqual(
            await wm.first_time(
                self.db, GUILD, self.campaign.id,
                [wm.Mark(self.place, "snow", self.climate, "hot")], now=90,
            ),
            [True],
        )  # fmt: skip

    async def other_campaign_place(self) -> tuple[str, str, str]:
        """A second campaign made the same way as the class's own, with its own place and climate
        row: (campaign id, place, climate)."""
        other = await self.campaigns.create(GUILD, "Two", DM)
        made = await self.memory.add_names(
            GUILD, other.id, [NewName("Frostmere", "place", CONFIRMED)], source="dm"
        )
        (place,) = made.value
        assert place is not None
        row = await self.climate_row("hot", place=place, campaign=other.id)
        return other.id, place, row

    async def test_taking_back_a_moment_leaves_another_campaigns_marks_alone(self) -> None:
        # (both campaigns' marks are the same kind, value and moment: only the campaign id tells
        # them apart, so this fails if `unmark_at` ignores the campaign)
        other_id, other_place, other_climate = await self.other_campaign_place()
        await wm.first_time(
            self.db, GUILD, self.campaign.id, [wm.Mark(self.place, "snow", self.climate, "hot")],
            now=40,
        )  # fmt: skip
        await wm.first_time(
            self.db, GUILD, other_id, [wm.Mark(other_place, "snow", other_climate, "hot")], now=40,
        )  # fmt: skip
        deleted = await wm.unmark_at(self.db, GUILD, self.campaign.id, 40)
        self.assertEqual(deleted, 1)
        self.assertEqual(await wm.noted_at(self.db, GUILD, self.campaign.id, 40), [])
        self.assertEqual(
            await wm.noted_at(self.db, GUILD, other_id, 40),
            [(other_place, "snow", "hot")],
        )

    async def test_taking_back_a_moment_leaves_another_moment_alone(self) -> None:
        # (both marks are on this campaign: only the moment tells them apart)
        await wm.first_time(
            self.db, GUILD, self.campaign.id, [wm.Mark(self.place, "snow", self.climate, "hot")],
            now=40,
        )  # fmt: skip
        await wm.first_time(
            self.db, GUILD, self.campaign.id, [wm.Mark(self.place, "frost", self.climate, "hot")],
            now=90,
        )  # fmt: skip
        self.assertEqual(await wm.unmark_at(self.db, GUILD, self.campaign.id, 40), 1)
        self.assertEqual(
            await wm.noted_at(self.db, GUILD, self.campaign.id, 90),
            [(self.place, "frost", "hot")],
        )

    async def test_taking_back_a_moment_with_nothing_there_deletes_nothing(self) -> None:
        await wm.first_time(
            self.db, GUILD, self.campaign.id, [wm.Mark(self.place, "snow", self.climate, "hot")],
            now=40,
        )  # fmt: skip
        self.assertEqual(await wm.unmark_at(self.db, GUILD, self.campaign.id, 77), 0)
        self.assertEqual(
            await wm.noted_at(self.db, GUILD, self.campaign.id, 40),
            [(self.place, "snow", "hot")],
        )


class TheBackup(unittest.TestCase):
    def test_a_note_out_of_shape_is_damage_and_one_for_nothing_held_is_dropped(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.memory.backup import _checked_rows

        row = {
            "table": "weather_note", "id": "a" * 32, "place_id": "b" * 32, "kind": "snow",
            "climate_id": "c" * 32, "climate": "hot", "created_at": 1,
        }  # fmt: skip
        # (well formed, but for a place and climate the file doesn't hold: dropped, never refused)
        self.assertEqual(_checked_rows([row])["weather_note"], [])
        bads: list[dict[str, Any]] = [
            {"kind": "rain"}, {"climate": ["hot"]}, {"kind": {}}, {"place_id": "x"},
        ]  # fmt: skip
        for bad in bads:
            with self.subTest(bad=bad), self.assertRaises(CampaignError):
                _checked_rows([{**row, **bad}])
