"""A House-rules file request from the website (#3087): the bot's half. The same link store and
comparison as Discord's 📄 buttons, the offer on the DM screen, the stage and counts the page
reads, and the link and the file's words blanked when the request ends. Invented names and
addresses; the file is fetched by a stand-in."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import fetch
from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import house_file_web as web
from dmbot.dm_screen import house_sync as hs
from dmbot.rules import house_file_queue as q
from dmbot.rules.house_file_link import HouseFileLinkStore
from dmbot.rules.house_file_queue import HouseFileQueue
from dmbot.ui import house_file as file_ui
from tests.pg import DatabaseTest
from tests.test_house_file_sync import FakeStore, rule

GUILD, SCREEN = 111, 30
DM, PLAYER = 7, 9
LINK = "https://docs.google.com/document/d/" + "a" * 30 + "/edit"
OTHER_LINK = "https://docs.google.com/document/d/" + "b" * 30 + "/edit"
# Against Keep / Old / Gone: one to add (9), one to change (2), one to remove (3).
FILE = "House rules: Frostmaiden\n1. Keep\n2. New words\n9. Fresh\n"
SECRET = "Fresh"  # a word of the file that must not be left in the queue


class Requests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        file_ui._last.clear()
        file_ui._busy.clear()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Strahd", DM)
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, SCREEN)
        self.links = HouseFileLinkStore(self.db)
        self.store = FakeStore([rule(1, "Keep"), rule(2, "Old"), rule(3, "Gone")])
        self.posts: list[tuple[int, str]] = []
        self.post_works = True

        async def post_message(channel: int, text: str, view: Any = None) -> Any:
            self.posts.append((channel, text))
            return SimpleNamespace(id=1) if self.post_works else None

        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
            house_rules=self.store,
            house_file_links=self.links,
            house_syncs=hs.Pendings(),
            post_message=post_message,
        )
        self.queue = HouseFileQueue(self.db)

    async def add(
        self, action: str, *, link: str = "", text: str = "", user: int = DM, campaign: Any = None
    ) -> str:
        now = int(time.time())
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "INSERT INTO web_house_file_actions (guild_id, campaign_id, id, user_id, action,"
                " link, text, created_at, updated_at) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, %s, %s, %s, %s, %s) RETURNING id",
                (GUILD, (campaign or self.campaign).id, user, action, link, text, now, now),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def row(self, request_id: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM web_house_file_actions WHERE guild_id = %s AND id = %s",
                (GUILD, request_id),
            )
            return dict(await cur.fetchone() or {})

    async def work(self, fetched: Any = None) -> q.Claim:
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        got = fetched or fetch.Fetched(FILE.encode(), "link.txt")
        mock = (
            AsyncMock(side_effect=got)
            if isinstance(got, Exception)
            else AsyncMock(return_value=got)
        )
        with patch.object(fetch, "fetch", mock):
            await web.run_request(self.bot, self.queue, claim, GUILD)
        return claim

    async def finished(self, request_id: str) -> tuple[str, str, str]:
        done = await self.row(request_id)
        return done["state"], done["stage"], done["error"]


class EachAction(Requests):
    async def test_link_sets_the_file_compares_it_and_posts_the_offer(self) -> None:
        rid = await self.add("link", link=LINK)
        await self.work()
        self.assertEqual(await self.finished(rid), ("done", "compared", ""))
        done = await self.row(rid)
        self.assertEqual((done["to_add"], done["to_change"], done["to_remove"]), (1, 1, 1))
        self.assertEqual(done["rules_read"], 3)
        stored = await self.links.get(GUILD, self.campaign.id)
        assert stored is not None
        self.assertEqual(stored.link, LINK)
        [(channel, text)] = self.posts
        self.assertEqual(channel, SCREEN)
        self.assertIn("1 to add, 1 to change, 1 to remove", text)
        self.assertEqual(len(self.store.rules), 3)  # nothing changes until a DM presses a button

    async def test_unlink_clears_the_link_and_keeps_the_rules(self) -> None:
        await self.links.set_link(GUILD, self.campaign.id, DM, LINK)
        rid = await self.add("unlink")
        await self.work()
        self.assertEqual(await self.finished(rid), ("done", "done", ""))
        self.assertIsNone(await self.links.get(GUILD, self.campaign.id))
        self.assertEqual((len(self.store.rules), self.posts), (3, []))

    async def test_check_compares_the_linked_file_now(self) -> None:
        await self.links.set_link(GUILD, self.campaign.id, DM, LINK)
        rid = await self.add("check")
        await self.work()
        self.assertEqual(await self.finished(rid), ("done", "compared", ""))
        self.assertEqual(len(self.posts), 1)

    async def test_check_with_no_file_linked_fails_as_a_bad_link(self) -> None:
        rid = await self.add("check")
        await self.work()
        self.assertEqual(await self.finished(rid), ("failed", "done", q.BAD_LINK))

    async def test_upload_compares_the_text_once_without_linking_it(self) -> None:
        rid = await self.add("upload", text=FILE)
        await self.work()
        self.assertEqual(await self.finished(rid), ("done", "compared", ""))
        self.assertIsNone(await self.links.get(GUILD, self.campaign.id))
        self.assertEqual(len(self.posts), 1)

    async def test_a_file_that_matches_is_done_with_no_offer(self) -> None:
        rid = await self.add("upload", text="1. Keep\n2. Old\n3. Gone\n")
        await self.work()
        self.assertEqual(await self.finished(rid), ("done", "compared", ""))
        done = await self.row(rid)
        self.assertEqual((done["to_add"], done["to_change"], done["to_remove"]), (0, 0, 0))
        self.assertEqual((done["rules_read"], self.posts), (3, []))


class WhatTheDmWordsDoNotKeep(Requests):
    async def test_the_link_and_the_text_are_blanked_whichever_way_it_ends(self) -> None:
        for action, kwargs in (
            ("link", {"link": LINK}),
            ("upload", {"text": FILE}),
            ("upload", {"text": "no rules in here"}),
        ):
            rid = await self.add(action, **kwargs)
            await self.work()
            done = await self.row(rid)
            self.assertEqual((done["link"], done["text"]), ("", ""), (action, kwargs))
            self.assertNotIn(SECRET, repr(done))

    async def test_nothing_of_the_link_or_the_file_is_logged(self) -> None:
        await self.add("link", link=LINK)
        with self.assertLogs("dmbot", level="DEBUG") as logs:
            await self.work()
            web.log.info("end of the request")  # (assertLogs needs one record to exist)
        shown = "\n".join(logs.output)
        self.assertNotIn("a" * 30, shown)
        self.assertNotIn(SECRET, shown)

    async def test_a_request_nobody_takes_is_blanked_by_the_sweep(self) -> None:
        rid = await self.add("upload", text=FILE)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE web_house_file_actions SET updated_at = updated_at - 2000")
        await self.queue.sweep(GUILD)
        done = await self.row(rid)
        self.assertEqual((done["state"], done["error"], done["text"]), ("failed", "not_taken", ""))


class WhyItStops(Requests):
    async def test_a_sender_who_stopped_being_a_dm_is_refused_and_nothing_is_done(self) -> None:
        rid = await self.add("link", link=LINK, user=PLAYER)
        await self.work()
        self.assertEqual(await self.finished(rid), ("failed", "done", q.NOT_DM))
        self.assertEqual((await self.links.get(GUILD, self.campaign.id), self.posts), (None, []))

    async def test_a_link_that_is_not_one_fails_as_a_bad_link(self) -> None:
        rid = await self.add("link", link="https://example.com/x")
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        with patch.object(self.links, "set_link", AsyncMock(side_effect=web.HouseRuleError("no"))):
            await web.run_request(self.bot, self.queue, claim, GUILD)
        self.assertEqual(await self.finished(rid), ("failed", "done", q.BAD_LINK))

    async def test_a_file_that_cannot_be_fetched_is_unreadable(self) -> None:
        rid = await self.add("link", link=LINK)
        await self.work(fetch.LinkError("That file isn't shared."))
        self.assertEqual(await self.finished(rid), ("failed", "done", q.UNREADABLE))

    async def test_a_file_with_no_rules_is_never_compared(self) -> None:
        rid = await self.add("upload", text="Just some thoughts about the game.")
        await self.work()
        self.assertEqual(await self.finished(rid), ("failed", "done", q.NO_RULES))
        self.assertEqual(self.posts, [])

    async def test_a_file_too_big_is_too_big(self) -> None:
        rid = await self.add("upload", text="x")
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        with patch.object(hs, "compare_text", AsyncMock(return_value=hs.TOO_BIG)):
            await web.run_request(self.bot, self.queue, claim, GUILD)
        self.assertEqual(await self.finished(rid), ("failed", "done", q.TOO_BIG))

    async def test_every_comparison_word_maps_to_its_page_word(self) -> None:
        for words, word in (
            (hs.couldnt_read("x"), q.UNREADABLE),
            (hs.NOT_READ, q.UNREADABLE),
            (hs.no_rules(2), q.NO_RULES),
            (hs.BOOK, q.NO_RULES),
            (hs.TOO_BIG, q.TOO_BIG),
            (hs.TOO_MANY_RULES, q.TOO_BIG),
            (hs.TOO_MANY_RULES_LINKED, q.TOO_BIG),
            (hs.NO_AI_PLAN, q.NO_AI),
            (hs.BUSY, q.BUSY),
            (hs.TOO_MANY_READS, q.DAILY_LIMIT),
            (hs.FAILED, q.FAILED),
            ("something new", q.FAILED),
        ):
            self.assertEqual(web.error_of(words), word, words)

    async def test_an_offer_that_cannot_be_posted_fails_as_no_offer(self) -> None:
        self.post_works = False
        rid = await self.add("upload", text=FILE)
        await self.work()
        self.assertEqual(await self.finished(rid), ("failed", "done", q.NO_OFFER))

    async def test_a_campaign_with_no_dm_screen_fails_as_no_offer(self) -> None:
        rid = await self.add("upload", text=FILE, campaign=self.other)
        await self.work()
        self.assertEqual(await self.finished(rid), ("failed", "done", q.NO_OFFER))
        self.assertEqual(self.posts, [])

    async def test_a_comparison_already_running_is_busy_and_one_just_made_is_too_often(
        self,
    ) -> None:
        file_ui._busy.add((GUILD, self.campaign.id))
        busy = await self.add("upload", text=FILE)
        await self.work()
        self.assertEqual(await self.finished(busy), ("failed", "done", q.BUSY))
        file_ui._busy.clear()
        first = await self.add("upload", text=FILE)
        await self.work()
        again = await self.add("upload", text=FILE)
        await self.work()
        self.assertEqual(await self.finished(first), ("done", "compared", ""))
        self.assertEqual(await self.finished(again), ("failed", "done", q.TOO_OFTEN))

    async def test_the_turn_is_given_back_when_a_request_breaks(self) -> None:
        rid = await self.add("upload", text=FILE)
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        with patch.object(hs, "compare_text", AsyncMock(side_effect=RuntimeError("boom"))):
            await web.run_request(self.bot, self.queue, claim, GUILD)
        self.assertEqual(await self.finished(rid), ("failed", "done", q.FAILED))
        self.assertNotIn((GUILD, self.campaign.id), file_ui._busy)


class TheQueue(Requests):
    async def test_a_claim_is_taken_once_and_the_same_row_twice_changes_nothing_more(self) -> None:
        rid = await self.add("unlink")
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        self.assertIsNone(await self.queue.claim_next(GUILD))
        await web.run_request(self.bot, self.queue, claim, GUILD)
        before = await self.row(rid)
        await web.run_request(self.bot, self.queue, claim, GUILD)  # a worker that lost the claim
        self.assertEqual(await self.row(rid), before)

    async def test_a_quiet_claim_goes_back_to_waiting_and_a_late_worker_writes_nothing(
        self,
    ) -> None:
        rid = await self.add("upload", text=FILE)
        first = await self.queue.claim_next(GUILD)
        assert first is not None
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE web_house_file_actions SET updated_at = updated_at - 700")
        await self.queue.sweep(GUILD)
        self.assertEqual((await self.row(rid))["state"], "waiting")
        second = await self.queue.claim_next(GUILD)
        assert second is not None
        self.assertFalse(await self.queue.finish(GUILD, first, error=q.FAILED))
        self.assertEqual((await self.row(rid))["state"], "working")
        self.assertTrue(await self.queue.finish(GUILD, second))

    async def test_another_servers_requests_are_not_taken(self) -> None:
        await self.add("unlink")
        self.assertIsNone(await self.queue.claim_next(GUILD + 1))

    async def test_a_keepalive_stage_is_written(self) -> None:
        rid = await self.add("upload", text=FILE)
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        self.assertTrue(await self.queue.progress(GUILD, claim, q.COMPARING))
        self.assertEqual((await self.row(rid))["stage"], q.COMPARING)

    async def test_a_cancel_puts_the_claim_back_for_the_next_start(self) -> None:
        import asyncio

        rid = await self.add("upload", text=FILE)
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        started = asyncio.Event()

        async def hang(*_a: Any, **_k: Any) -> str:
            started.set()
            await asyncio.sleep(60)
            return ""

        with patch.object(hs, "compare_text", hang):
            task = asyncio.ensure_future(web.run_request(self.bot, self.queue, claim, GUILD))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((await self.row(rid))["state"], "waiting")
