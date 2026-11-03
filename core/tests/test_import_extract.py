"""The story read of an imported session (#3301): it runs after the name scan, keeps the scan's
claim fresh, waits for a later round when the call limit is used, and is never done twice.
Invented names and ids."""

from __future__ import annotations

import time
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.transcript.import_job import STALE_SCAN_S
from tests.test_import_recaps import DAY, GUILD, START, WithImports

BOT: Any = DMBot  # (the methods are called on stand-ins)


class TheStore(WithImports):
    async def test_a_running_scan_is_renewed_and_a_finished_one_is_not(self) -> None:
        sid = await self.imported(1)
        now = int(time.time())
        self.assertFalse(await self.store.renew_scan(GUILD, sid, now))  # (not claimed yet)
        await self.store.claim_scan(GUILD, sid, now - 600)
        self.assertTrue(await self.store.renew_scan(GUILD, sid, now))
        await self.store.finish_scan(GUILD, sid, now)
        self.assertFalse(await self.store.renew_scan(GUILD, sid, now + 1))

    async def test_scanned_sessions_not_yet_read_are_listed_oldest_first_and_read_ones_are_not(
        self,
    ) -> None:
        late, early = await self.imported(2), await self.imported(1)
        self.assertEqual(await self.store.unextracted(GUILD), [])  # (not scanned yet)
        for sid in (early, late):
            await self.store.claim_scan(GUILD, sid, int(time.time()))
            await self.store.finish_scan(GUILD, sid, int(time.time()))
        self.assertEqual([r.session_id for r in await self.store.unextracted(GUILD)], [early])
        await self.transcripts.record_extraction(
            GUILD, early, through_ms=(START + DAY) * 1000, calls=1, now=1
        )
        self.assertEqual([r.session_id for r in await self.store.unextracted(GUILD)], [late])

    async def test_renewing_keeps_the_window_start_where_the_scan_began(self) -> None:
        sid = await self.imported(1)
        began = int(time.time()) - 600
        await self.store.claim_scan(GUILD, sid, began)
        await self.store.renew_scan(GUILD, sid, began + 400)
        self.assertEqual(await self.store.window(GUILD, sid), (began, began + 400))
        # a renewed claim is not stale; one nobody renewed is
        self.assertFalse(await self.store.claim_scan(GUILD, sid, began + 400 + STALE_SCAN_S - 1))
        self.assertTrue(await self.store.claim_scan(GUILD, sid, began + 400 + STALE_SCAN_S + 1))

    async def test_each_campaign_lists_only_its_earliest_unread_session_and_all_are_counted(
        self,
    ) -> None:
        early, mid, late = [await self.imported(d) for d in (1, 2, 3)]
        for sid in (early, mid, late):
            await self.store.claim_scan(GUILD, sid, int(time.time()))
            await self.store.finish_scan(GUILD, sid, int(time.time()))
        self.assertEqual([r.session_id for r in await self.store.unextracted(GUILD)], [early])
        self.assertEqual(
            await self.store.count_unextracted(GUILD), {self.campaign.id: 3}
        )  # (the count is not capped)

    async def test_an_import_scanned_before_the_story_read_existed_is_left_alone(self) -> None:
        sid = await self.imported(1)
        await self.store.claim_scan(GUILD, sid, 1_700_000_000)
        await self.store.finish_scan(GUILD, sid, 1_700_000_100)
        self.assertEqual(await self.store.unextracted(GUILD), [])

    def test_one_extractor_call_always_fits_inside_the_scans_stale_window(self) -> None:
        from dmbot.memory.extractor import CALL_TIMEOUT_S

        self.assertLess(CALL_TIMEOUT_S * 2, STALE_SCAN_S)  # (the claim is renewed before each call)

    async def test_a_session_whose_scan_was_given_up_is_not_read(self) -> None:
        sid = await self.imported(1)
        await self.store.give_up_scan(GUILD, sid)
        self.assertEqual(await self.store.unextracted(GUILD), [])


def bot_for(store: Any, outcomes: list[str], **kw: Any) -> Any:
    sent: list[Any] = []

    async def extract(gid: int, ready: Any) -> str:
        sent.append(ready.session_id)
        return outcomes.pop(0)

    me = SimpleNamespace(
        import_job=SimpleNamespace(store=store),
        transcripts=SimpleNamespace(),
        extractor_ai=object(),
        settings=SimpleNamespace(story_extractor_on=True),
        _import_reading=set(),
        _import_left_told={},
        _extract_import=extract,
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_screen_channel_id=9))
        ),
        post=AsyncMock(),
        sent=sent,
        **kw,
    )
    me._extract_imports_left = partial(BOT._extract_imports_left, me)
    me._tell_imports_left = partial(BOT._tell_imports_left, me)
    return me


class TheSweep(WithImports):
    async def scanned(self, *days: int) -> list[str]:
        ids = [await self.imported(d) for d in days]
        for sid in ids:
            await self.store.claim_scan(GUILD, sid, int(time.time()))
            await self.store.finish_scan(GUILD, sid, int(time.time()))
        return ids

    async def test_each_round_reads_the_earliest_unread_session_of_a_campaign_and_counts_the_rest(
        self,
    ) -> None:
        one, two, three = await self.scanned(1, 2, 3)
        me = bot_for(self.store, ["limit"])
        left = await me._extract_imports_left(GUILD)
        self.assertEqual(me.sent, [one])  # (the later sessions are not read before the earlier)
        self.assertEqual(left, {self.campaign.id: 3})  # (all three still wait: the count is real)
        del two, three

    async def test_nothing_is_read_while_the_story_read_is_off_or_a_sweep_is_running(self) -> None:
        await self.scanned(1)
        off = bot_for(self.store, ["done"])
        off.settings.story_extractor_on = False
        self.assertEqual(await off._extract_imports_left(GUILD), {})
        busy = bot_for(self.store, ["done"])
        busy._import_reading.add(GUILD)
        self.assertEqual(await busy._extract_imports_left(GUILD), {})
        self.assertEqual((off.sent, busy.sent), ([], []))

    async def test_the_dm_is_told_once_for_each_change_and_only_after_the_scans(self) -> None:
        me = bot_for(self.store, [])
        cid = self.campaign.id
        await me._tell_imports_left(GUILD, self.store, {cid: 2})
        await me._tell_imports_left(GUILD, self.store, {cid: 2})  # (same count: nothing new)
        self.assertEqual(me.post.await_count, 1)
        self.assertIn("2 imported sessions", me.post.await_args.args[1])
        await me._tell_imports_left(GUILD, self.store, {cid: 1})
        self.assertEqual(me.post.await_count, 2)
        await me._tell_imports_left(GUILD, self.store, {})  # (all read: no word)
        self.assertEqual(me.post.await_count, 2)
        self.assertEqual(me._import_left_told, {})

    async def test_nothing_is_said_while_a_scan_is_still_waiting(self) -> None:
        await self.imported(1)  # (cleaned, not scanned)
        me = bot_for(self.store, [])
        await me._tell_imports_left(GUILD, self.store, {self.campaign.id: 2})
        me.post.assert_not_awaited()


class TheImportRead(WithImports):
    def fake(self, outcome: str) -> Any:
        me = SimpleNamespace(
            import_job=SimpleNamespace(store=self.store),
            campaigns=SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(dm_user_ids=frozenset({5}), dm_screen_channel_id=9)
                )
            ),
            transcripts=self.transcripts,
            _extract_session=AsyncMock(return_value=outcome),
        )
        me._mark_import_read = partial(BOT._mark_import_read, me)
        return me

    async def test_the_scans_claim_is_renewed_before_each_call_and_a_finished_read_is_marked(
        self,
    ) -> None:
        sid = await self.imported(1)
        await self.store.claim_scan(GUILD, sid, int(time.time()) - 600)
        ready: Any = SimpleNamespace(campaign_id=self.campaign.id, session_id=sid)
        me = self.fake("done")
        self.assertEqual(await BOT._extract_import(me, GUILD, ready), "done")
        kwargs = me._extract_session.await_args.kwargs
        self.assertEqual(kwargs["dm_ids"], {5})
        self.assertIsNone(kwargs["tally"])
        self.assertTrue(await kwargs["renew"]())  # (the claim is still ours and now fresh)
        through, _ = await self.transcripts.extraction(GUILD, sid)
        self.assertEqual(through, START * 1000 + DAY * 1000)  # marked read to its last line

    async def test_a_session_stopped_by_the_limit_is_not_marked_read(self) -> None:
        sid = await self.imported(1)
        ready: Any = SimpleNamespace(campaign_id=self.campaign.id, session_id=sid)
        me = self.fake("limit")
        self.assertEqual(await BOT._extract_import(me, GUILD, ready), "limit")
        self.assertEqual(await self.transcripts.extraction(GUILD, sid), (0, 0))

    async def test_a_failed_read_is_logged_by_type_and_never_raises(self) -> None:
        sid = await self.imported(1)
        ready: Any = SimpleNamespace(campaign_id=self.campaign.id, session_id=sid)
        me = self.fake("done")
        me._extract_session = AsyncMock(side_effect=RuntimeError("secret words"))
        with patch("dmbot.bot.log") as log:
            self.assertEqual(await BOT._extract_import(me, GUILD, ready), "skipped")
        self.assertNotIn("secret words", str(log.warning.call_args))
