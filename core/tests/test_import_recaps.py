"""The name scan and the recap of an imported session (#2106): in date order, once each, and the
recap waits for the DM's name answers through the same hold as a live session (#2174). Invented
names and ids throughout."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.transcript.import_job import ImportStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, DM, MIRA = 77, 5, 7001
DAY = 86_400
START = 1_700_000_000


class WithImports(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.consent = ConsentStore(self.db)
        await self.consent.grant(GUILD, MIRA)
        self.transcripts = TranscriptStore(self.db)
        self.store = ImportStore(self.db)

    async def imported(
        self, day: int, *, state: str = "done", recap: bool = False, said: str = "Kazeth rides."
    ) -> str:
        sid = await self.transcripts.open_session(GUILD, self.campaign.id, START + day * DAY)
        await self.transcripts.add_lines(
            GUILD, sid, [Line((START + day * DAY) * 1000, MIRA, said, said)]
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at, state, write_recap)"
                " VALUES (%s, %s, %s, %s, 'a.txt', %s, 1, %s, %s)",
                (GUILD, self.campaign.id, sid, "a" * 32, DM, state, recap),
            )
        return sid


class TheStore(WithImports):
    async def test_scans_go_in_date_order_one_per_campaign(self) -> None:
        late = await self.imported(2)
        early = await self.imported(1)
        ready = await self.store.ready_for_scan(GUILD)
        self.assertEqual([r.session_id for r in ready], [early])  # the later one waits its turn
        self.assertTrue(await self.store.claim_scan(GUILD, early, 10))
        self.assertEqual([r.session_id for r in await self.store.ready_for_scan(GUILD)], [late])

    async def test_an_earlier_session_still_being_cleaned_holds_the_later_ones_back(self) -> None:
        await self.imported(1, state="working")
        await self.imported(2)
        self.assertEqual(await self.store.ready_for_scan(GUILD), [])

    async def test_a_failed_earlier_session_does_not_block_the_rest(self) -> None:
        await self.imported(1, state="failed")
        later = await self.imported(2)
        self.assertEqual([r.session_id for r in await self.store.ready_for_scan(GUILD)], [later])

    async def test_a_scan_is_claimed_once_and_its_window_is_kept(self) -> None:
        sid = await self.imported(1)
        self.assertIsNone(await self.store.window(GUILD, sid))
        self.assertTrue(await self.store.claim_scan(GUILD, sid, 100))
        self.assertFalse(await self.store.claim_scan(GUILD, sid, 300))  # once
        await self.store.finish_scan(GUILD, sid, 160)
        self.assertEqual(await self.store.window(GUILD, sid), (100, 160))
        self.assertEqual(await self.store.ready_for_scan(GUILD), [])

    async def test_a_failed_scan_is_given_back_with_a_count_and_can_be_let_go(self) -> None:
        sid = await self.imported(1)
        await self.store.claim_scan(GUILD, sid, 100)
        self.assertEqual(await self.store.release_scan(GUILD, sid), 1)
        self.assertEqual([r.session_id for r in await self.store.ready_for_scan(GUILD)], [sid])
        await self.store.claim_scan(GUILD, sid, 200)
        self.assertEqual(await self.store.release_scan(GUILD, sid), 2)
        await self.store.give_up_scan(GUILD, sid)
        self.assertEqual(await self.store.window(GUILD, sid), (0, 0))  # no questions to wait on
        self.assertEqual(await self.store.ready_for_scan(GUILD), [])

    async def test_a_recap_is_ready_once_scanned_asked_for_and_not_yet_started(self) -> None:
        sid = await self.imported(1, recap=True)
        self.assertEqual(await self.store.ready_for_recap(GUILD), [])  # not scanned yet
        await self.store.claim_scan(GUILD, sid, 100)
        self.assertEqual([r.session_id for r in await self.store.ready_for_recap(GUILD)], [sid])
        await self.store.mark_recap_started(GUILD, sid)
        self.assertEqual(await self.store.ready_for_recap(GUILD), [])
        other = await self.imported(2)  # (the DM did not ask for this one's recap)
        await self.store.claim_scan(GUILD, other, 100)
        self.assertEqual(await self.store.ready_for_recap(GUILD), [])

    async def test_the_choice_travels_with_the_session(self) -> None:
        await self.imported(1, recap=True)
        (ready,) = await self.store.ready_for_scan(GUILD)
        self.assertTrue(ready.write_recap)

    async def test_the_scan_reads_cleaned_game_talk_only(self) -> None:
        sid = await self.imported(1, said="we meet kazeth")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET text = 'we meet Kazeth' WHERE session_id = %s", (sid,)
            )
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " duration_ms, topic) VALUES (%s, %s, 5, %s, 'my boss called', 1000, 'off_topic')",
                (GUILD, sid, MIRA),
            )
        self.assertEqual(await self.store.scan_lines(GUILD, sid), [(MIRA, "we meet Kazeth")])


def fake_bot(store: ImportStore, **kw: Any) -> Any:
    campaign = SimpleNamespace(
        guild_id=GUILD, id="", recaps_text=True, recaps_email=True, owner_user_id=DM,
        dm_user_ids=frozenset({DM}), dm_screen_channel_id=66,
    )  # fmt: skip
    bot = SimpleNamespace(
        settings=SimpleNamespace(recaps_on=True),
        import_job=SimpleNamespace(store=store),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        memory=object(),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={MIRA})),
        recaps=SimpleNamespace(enqueue=AsyncMock(return_value=True)),
        plan_allows=AsyncMock(return_value=True),
        _propose_new_names=AsyncMock(return_value=["Kazeth"]),
        _hold_or_queue=AsyncMock(return_value="held"),
        **kw,
    )
    for name in ("run_import_followups", "_scan_import", "_import_recap"):
        setattr(bot, name, getattr(DMBot, name).__get__(bot))
    return bot, campaign


async def rounds(bot: Any, n: int = 4) -> None:
    for _ in range(n):  # one session per campaign each round
        await bot.run_import_followups(GUILD)


class TheFollowUp(WithImports):
    async def bot(self, **kw: Any) -> tuple[Any, Any]:
        bot, campaign = fake_bot(self.store, **kw)
        campaign.id = self.campaign.id
        return bot, campaign

    async def test_each_session_is_scanned_once_in_date_order_and_only_asked_ones_get_recaps(
        self,
    ) -> None:
        a = await self.imported(1, recap=True)
        b = await self.imported(2)
        c = await self.imported(3, recap=True)
        bot, _ = await self.bot()
        await rounds(bot)
        self.assertEqual(bot._propose_new_names.await_count, 3)  # one scan for each session
        scanned = [await self.store.window(GUILD, x) for x in (a, b, c)]
        self.assertTrue(all(w is not None for w in scanned))
        asked = [call.args[1] for call in bot._hold_or_queue.await_args_list]
        self.assertEqual(asked, [a, c])  # b was not asked for
        for call in bot._hold_or_queue.await_args_list:
            self.assertFalse(call.kwargs["extractor_to_come"])  # no extractor for an import
            self.assertEqual(call.args[3], 66)  # asked on the campaign's DM screen
        starts = sorted(call.args[2] for call in bot._hold_or_queue.await_args_list)
        self.assertEqual(starts, [START + DAY, START + 3 * DAY])

    async def test_the_scan_asks_only_about_lines_of_people_who_still_agree(self) -> None:
        await self.imported(1)
        bot, _ = await self.bot()
        bot.consent.consenting = AsyncMock(return_value=set())  # Mira stopped being recorded
        await rounds(bot, 1)
        bot._propose_new_names.assert_not_awaited()

    async def test_who_agrees_is_asked_after_the_lines_are_read(self) -> None:
        await self.imported(1)
        bot, _ = await self.bot()
        order: list[str] = []
        real = self.store.scan_lines

        async def lines(*a: Any) -> Any:
            order.append("lines")
            return await real(*a)

        async def agreed(*a: Any) -> Any:
            order.append("consent")
            return {MIRA}

        self.store.scan_lines = lines  # type: ignore[method-assign]
        bot.consent.consenting = agreed
        await rounds(bot, 1)
        self.assertEqual(order, ["lines", "consent"])  # a stop during the read is not missed

    async def test_each_gate_stops_the_recap_on_its_own(self) -> None:
        cases = {
            "recaps off": lambda b, c: setattr(b.settings, "recaps_on", False),
            "the DM chose none": lambda b, c: setattr(c, "recaps_text", False),
            "the plan lacks it": lambda b, c: setattr(
                b, "plan_allows", AsyncMock(return_value=False)
            ),
            "no DM": lambda b, c: (
                setattr(c, "owner_user_id", 0),
                setattr(c, "dm_user_ids", frozenset()),
            ),
        }
        for name, change in cases.items():
            with self.subTest(name):
                await self.imported(1, recap=True)
                bot, campaign = await self.bot()
                change(bot, campaign)
                await rounds(bot, 1)
                bot._hold_or_queue.assert_not_awaited()
                bot.recaps.enqueue.assert_not_awaited()
                async with self.db.guild(GUILD) as conn:  # decided against: not asked again
                    await conn.execute("DELETE FROM session_imports")
                    await conn.execute("DELETE FROM transcript_sessions")

    async def test_without_a_dm_screen_the_recap_is_queued_without_a_hold(self) -> None:
        await self.imported(1, recap=True)
        bot, campaign = await self.bot()
        campaign.dm_screen_channel_id = None
        await rounds(bot, 1)
        bot._hold_or_queue.assert_not_awaited()
        bot.recaps.enqueue.assert_awaited_once()

    async def test_a_scan_that_fails_is_tried_again_next_round(self) -> None:
        sid = await self.imported(1, recap=True)
        bot, _ = await self.bot()
        bot._propose_new_names = AsyncMock(side_effect=[RuntimeError("memory"), ["Kazeth"]])
        await rounds(bot, 1)
        self.assertEqual(len(await self.store.ready_for_scan(GUILD)), 1)  # given back
        bot._hold_or_queue.assert_not_awaited()
        await rounds(bot, 2)
        self.assertIsNotNone(await self.store.window(GUILD, sid))
        bot._hold_or_queue.assert_awaited_once()

    async def test_a_scan_that_always_fails_is_let_go_and_blocks_nothing_after_it(self) -> None:
        stuck = await self.imported(1)
        later = await self.imported(2, recap=True)
        bot, _ = await self.bot()
        bot._propose_new_names = AsyncMock(
            side_effect=lambda gid, cid, lines, users: (
                (_ for _ in ()).throw(RuntimeError("memory")) if "Kazeth rides." in lines else []
            )
        )
        await rounds(bot, 6)
        self.assertEqual(await self.store.window(GUILD, stuck), (0, 0))  # let go, no questions
        self.assertIsNotNone(await self.store.window(GUILD, later))  # the next one went on
        asked = [call.args[1] for call in bot._hold_or_queue.await_args_list]
        self.assertEqual(asked, [later])

    async def test_a_recap_that_could_not_start_is_tried_again_and_the_scan_is_not_redone(
        self,
    ) -> None:
        await self.imported(1, recap=True)
        bot, _ = await self.bot()
        bot._hold_or_queue = AsyncMock(side_effect=[RuntimeError("discord"), "held"])
        await rounds(bot, 3)
        self.assertEqual(bot._hold_or_queue.await_count, 2)  # tried again after the failure
        self.assertEqual(bot._propose_new_names.await_count, 1)  # the scan was not redone

    async def test_two_bots_do_not_both_scan_one_session(self) -> None:
        sid = await self.imported(1)
        await self.store.claim_scan(GUILD, sid, 1)  # another bot took it
        bot, _ = await self.bot()
        await rounds(bot, 1)
        bot._propose_new_names.assert_not_awaited()


class TheQuestionCount(WithImports):
    """The recap of an imported session waits only on the questions its own scan raised."""

    async def test_an_imported_sessions_count_uses_its_scan_window(self) -> None:
        sid = await self.imported(1)
        now = int(time.time())
        await self.store.claim_scan(GUILD, sid, now)
        await self.store.finish_scan(GUILD, sid, now + 5)
        proposals = [
            SimpleNamespace(created_at=now - 100),  # before this scan: another session's
            SimpleNamespace(created_at=now + 2),  # inside it
            SimpleNamespace(created_at=now + 5),  # at its last second
            SimpleNamespace(created_at=now + 50),  # after it: the next session's
        ]
        bot = SimpleNamespace(
            import_job=SimpleNamespace(store=self.store),
            memory=SimpleNamespace(entities=AsyncMock(return_value=proposals)),
            heard_questions=None,
            _next_session_start=AsyncMock(return_value=None),
        )
        count = await DMBot._open_name_questions(bot, GUILD, self.campaign.id, sid, START + DAY)  # type: ignore[arg-type]
        self.assertEqual(count, 2)
        bot._next_session_start.assert_not_awaited()  # the window is the rule, not the next start

    async def test_a_live_session_still_counts_from_its_start_to_the_next_start(self) -> None:
        bot = SimpleNamespace(
            import_job=SimpleNamespace(store=self.store),
            memory=SimpleNamespace(
                entities=AsyncMock(
                    return_value=[SimpleNamespace(created_at=t) for t in (50, 150, 250)]
                )
            ),
            heard_questions=None,
            _next_session_start=AsyncMock(return_value=200),
        )
        count = await DMBot._open_name_questions(bot, GUILD, self.campaign.id, "live", 100)  # type: ignore[arg-type]
        self.assertEqual(count, 1)  # only the one made in [100, 200)


if __name__ == "__main__":
    unittest.main()
