"""A copy of a campaign, built by the bot and downloaded from the website (#2554, part 1b): who may
ask, the shared progress shape, the file once, the plan and size refusals, the 15-minute limit."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

from dmbot.campaigns import web_backup
from dmbot.campaigns.store import BackupTooBig, decode_backup
from dmbot.campaigns.web_backup import WebBackups
from dmbot.web import campaign_backup, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

LOST = 777  # a server Alice's session does not have


class Backing(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lost = await self.campaigns.create(LOST, "Lost World", ALICE.id)
        self.backups = WebBackups(self.db)

    async def signed(self, user: Any, *guilds: Any) -> Any:
        token = await sessions.sign_in(self.web, user, list(guilds), now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    def cref(self, guild: int, campaign: Any) -> str:
        return f"{guild}.{campaign.id}"

    async def refused(self, session: Any, ref: str) -> str:
        with self.assertRaises(RecapError) as caught:
            await campaign_backup.request(self.web, session, ref)
        return caught.exception.code

    def host(self, *, plan: str | None = None) -> Any:
        async def get_campaign(g: int, c: str) -> Any:
            return await self.campaigns.get(g, c)

        async def plan_allows(
            action: str, g: int, campaign: Any, *, fail_open: bool = True
        ) -> bool:
            assert not fail_open
            return plan is None

        async def export_campaign(g: int, c: str) -> Any:
            return await self.campaigns.export(g, c)

        return SimpleNamespace(
            get_campaign=get_campaign, plan_allows=plan_allows, export_campaign=export_campaign
        )

    async def build_next(self, guild: int, host: Any = None) -> str:
        item = await self.backups.claim_next(guild)
        assert item is not None
        return await web_backup.build(host or self.host(), guild, item, self.backups)

    # ---- who may ask ----

    async def test_the_owner_may_ask_in_any_server_even_one_the_session_does_not_have(self) -> None:
        for guild, campaign in ((THURSDAY.id, self.one), (LOST, self.lost)):
            made = await campaign_backup.request(self.web, self.session, self.cref(guild, campaign))
            self.assertRegex(made, r"^[0-9a-f]{32}$")

    async def test_a_game_master_may_ask_in_a_server_on_their_list_only(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        await self.campaigns.add_dm(LOST, self.lost.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        self.assertRegex(
            await campaign_backup.request(self.web, bob, self.cref(THURSDAY.id, self.one)),
            r"^[0-9a-f]{32}$",
        )
        self.assertEqual(await self.refused(bob, self.cref(LOST, self.lost)), "not_found")

    async def test_a_player_and_another_account_get_nothing(self) -> None:
        bob = await self.signed(BOB, THURSDAY)  # not a game master of anything
        self.assertEqual(await self.refused(bob, self.cref(THURSDAY.id, self.one)), "not_found")
        self.assertEqual(await self.refused(bob, "nonsense"), "not_found")

    async def test_asking_again_while_one_is_on_the_way_gives_the_same_copy(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        first = await campaign_backup.request(self.web, self.session, ref)
        self.assertEqual(await campaign_backup.request(self.web, self.session, ref), first)

    async def test_more_than_five_in_the_window_is_too_soon(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        for _ in range(5):
            made = await campaign_backup.request(self.web, self.session, ref)
            await self.db_sql(THURSDAY.id, "UPDATE web_backups SET state = 'failed'")
            self.assertTrue(made)
        self.assertEqual(await self.refused(self.session, ref), "too_soon")

    async def test_fetching_a_copy_does_not_reset_the_count_of_requests(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        for _ in range(5):
            made = await campaign_backup.request(self.web, self.session, ref)
            self.assertEqual(await self.build_next(THURSDAY.id), "")
            await campaign_backup.fetch(self.web, self.session, ref, made)
        self.assertEqual(await self.refused(self.session, ref), "too_soon")

    async def test_a_copy_older_than_fifteen_minutes_is_not_served(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        self.assertEqual(await self.build_next(THURSDAY.id), "")
        await self.db_sql(THURSDAY.id, "UPDATE web_backups SET created_at = created_at - 1000")
        with self.assertRaises(RecapError):
            await campaign_backup.fetch(self.web, self.session, ref, made)

    async def test_the_website_role_cannot_read_the_table_itself(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT has_table_privilege('dmbot_web', 'web_backups', 'SELECT') AS can"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["can"])

    async def db_sql(self, guild: int, statement: str, *args: Any) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(statement, args)

    # ---- the copy ----

    async def test_the_bot_builds_a_copy_a_restore_takes_and_the_page_gets_it_once(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        waiting = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual(
            (waiting["stage"], waiting["state"], waiting["ready"]), ("waiting", "running", False)
        )
        self.assertEqual(
            sorted(waiting),
            [
                "expiresAt",
                "fileName",
                "ready",
                "reason",
                "serverNow",
                "size",
                "stage",
                "startedAt",
                "state",
                "updatedAt",
            ],
        )
        self.assertEqual(await self.build_next(THURSDAY.id), "")
        ready = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual((ready["stage"], ready["state"], ready["ready"]), ("done", "done", True))
        self.assertTrue(ready["fileName"].endswith(".dmbot.json.gz"))
        raw, name = await campaign_backup.fetch(self.web, self.session, ref, made)
        self.assertEqual(name, ready["fileName"])
        self.assertEqual(ready["size"], len(raw))
        backup = decode_backup(raw)
        self.assertEqual(backup["campaign"]["name"], "One")  # type: ignore[index]
        with self.assertRaises(RecapError):  # taken once
            await campaign_backup.fetch(self.web, self.session, ref, made)
        with self.assertRaises(RecapError):
            await campaign_backup.status(self.web, self.session, ref, made)

    async def test_a_campaign_in_a_server_the_bot_cannot_reach_is_still_built(self) -> None:
        ref = self.cref(LOST, self.lost)
        made = await campaign_backup.request(self.web, self.session, ref)
        self.assertEqual(await self.build_next(LOST), "")
        raw, _ = await campaign_backup.fetch(self.web, self.session, ref, made)
        self.assertEqual(decode_backup(raw)["campaign"]["name"], "Lost World")  # type: ignore[index]

    async def test_only_the_person_who_asked_can_read_or_fetch_it(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        for call in (campaign_backup.status, campaign_backup.fetch):
            with self.assertRaises(RecapError):
                await call(self.web, bob, ref, made)
        raw, _ = await campaign_backup.fetch(self.web, self.session, ref, made)  # still there
        self.assertTrue(raw)

    async def test_a_plan_with_no_copies_fails_with_the_plan_word(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        self.assertEqual(
            await self.build_next(THURSDAY.id, self.host(plan="Upgrade to ...")), "plan"
        )
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual((got["state"], got["reason"], got["ready"]), ("failed", "plan", False))

    async def test_a_sender_who_is_no_longer_a_game_master_is_refused_by_the_bot(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, bob, ref)
        await self.db_sql(THURSDAY.id, "DELETE FROM campaign_dms WHERE user_id = %s", BOB.id)
        self.assertEqual(await self.build_next(THURSDAY.id), "not_allowed")
        got = await campaign_backup.status(self.web, bob, ref, made)
        self.assertEqual(got["reason"], "not_allowed")

    async def test_a_campaign_too_big_for_a_restore_fails_and_keeps_nothing(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        with mock.patch.object(web_backup, "encode_backup", side_effect=BackupTooBig("big")):
            self.assertEqual(await self.build_next(THURSDAY.id), "too_big")
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual((got["state"], got["reason"]), ("failed", "too_big"))
        with self.assertRaises(RecapError):
            await campaign_backup.fetch(self.web, self.session, ref, made)

    async def test_a_failure_in_the_bot_is_a_word_not_a_trace(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)

        async def broken(g: int, c: str) -> Any:
            raise RuntimeError("secret detail")

        host = self.host()
        host.export_campaign = broken
        self.assertEqual(await self.build_next(THURSDAY.id, host), "failed")
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual(got["reason"], "failed")
        self.assertNotIn("secret", str(got))

    async def test_a_request_and_its_file_go_after_fifteen_minutes(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        later = WebBackups(self.db, clock=lambda: time.time() + web_backup.KEEP_FOR_S + 60)
        self.assertEqual(await later.sweep(THURSDAY.id), 1)
        with self.assertRaises(RecapError):
            await campaign_backup.fetch(self.web, self.session, ref, made)

    async def test_a_stuck_request_reads_stalled_in_the_shared_shape(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        got = await campaign_backup.status(
            self.web, self.session, ref, made, now=int(time.time()) + 600
        )
        self.assertEqual((got["state"], got["reason"]), ("stalled", "stalled"))

    async def test_a_deleted_campaign_takes_its_requests_with_it(self) -> None:
        await campaign_backup.request(self.web, self.session, self.cref(THURSDAY.id, self.one))
        await self.campaigns.delete(THURSDAY.id, self.one.id)
        self.assertIsNone(await self.backups.claim_next(THURSDAY.id))


class TheRoute(Backing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)

    async def test_ask_poll_and_download_over_http(self) -> None:
        base = f"/me/campaigns/{THURSDAY.id}.{self.one.id}/backup"
        asked = await self.client.post(base, headers=HEADERS)
        self.assertEqual(asked.status_code, 202, asked.text)
        made = asked.json()["id"]
        before = await self.client.get(f"{base}/{made}")
        self.assertEqual((before.status_code, before.json()["ready"]), (200, False))
        early = await self.client.get(f"{base}/{made}/file")
        self.assertEqual(early.status_code, 404)
        await self.build_next(THURSDAY.id)
        after = await self.client.get(f"{base}/{made}")
        self.assertTrue(after.json()["ready"])
        got = await self.client.get(f"{base}/{made}/file")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.headers["content-type"], "application/gzip")
        self.assertIn("attachment; filename=", got.headers["content-disposition"])
        self.assertEqual(got.headers["cache-control"], "no-store")
        self.assertEqual(decode_backup(got.content)["campaign"]["name"], "One")  # type: ignore[index]
        again = await self.client.get(f"{base}/{made}/file")
        self.assertEqual(again.status_code, 404)

    async def test_the_refusals_over_http(self) -> None:
        other = await self.client.post(f"/me/campaigns/{THURSDAY.id}.nope/backup", headers=HEADERS)
        self.assertEqual((other.status_code, other.json()["error"]), (404, "not_found"))
        bad = await self.client.get(f"/me/campaigns/{THURSDAY.id}.{self.one.id}/backup/zzz")
        self.assertEqual(bad.status_code, 404)
