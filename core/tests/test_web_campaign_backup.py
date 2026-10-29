"""A copy of a campaign, built by the bot and downloaded from the website (#2554, part 1b): who may
ask, the shared progress shape, the file once, the plan and size refusals, the 15-minute limit."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

from dmbot.bot import DMBot
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
            return await self.campaigns.export_set(g, c)

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
                "fileWaiting",
                "parts",
                "partsTotal",
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

    async def seed_transcripts(self, sessions: int = 2, lines: int = 3) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for n in range(sessions):
                sid = f"{n + 1:032x}"
                await conn.execute(
                    "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                    " ended_at, line_count, speakers) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (sid, THURSDAY.id, self.one.id, 1000 * (n + 1), 1000 * (n + 1) + 60, lines,
                     [ALICE.id]),
                )  # fmt: skip
                for k in range(lines):
                    await conn.execute(
                        "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                        " heard, text) VALUES (%s, %s, %s, %s, 'raw', %s)",
                        (THURSDAY.id, sid, k * 1000, ALICE.id, f"clean {n} {k}"),
                    )

    async def test_a_website_copy_carries_the_transcripts_as_discords_does(self) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        self.assertEqual(await self.build_next(THURSDAY.id), "")
        _, discord_parts = await self.campaigns.export_set(THURSDAY.id, self.one.id)
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual(got["partsTotal"], len(discord_parts))
        self.assertEqual([p["n"] for p in got["parts"]], list(range(1, len(discord_parts) + 1)))
        self.assertGreaterEqual(len(discord_parts), 1)
        raw, name = await campaign_backup.fetch(self.web, self.session, ref, made)
        files = []
        for p in got["parts"]:
            part, part_name = await campaign_backup.fetch_part(
                self.web, self.session, ref, made, p["n"]
            )
            self.assertEqual(len(part), p["size"])
            self.assertEqual(
                part_name, name.removesuffix(".dmbot.json.gz") + f".transcripts-{p['n']}-of-"
                f"{got['partsTotal']}.dmbot.json.gz",
            )  # fmt: skip
            files.append(part)
        # the same set: the main file names its parts, and a restore takes them
        manifest = decode_backup(raw)["parts"]  # type: ignore[index]
        self.assertEqual(manifest["count"], len(files))
        restored = await self.campaigns.import_backup(LOST, decode_backup(raw), BOB.id, parts=files)
        async with self.db.guild(LOST) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_lines WHERE guild_id = %s", (LOST,)
            )
            lines = (await cur.fetchone() or {})["n"]
        self.assertEqual((restored.name, lines), ("One", 6))

    async def test_each_transcripts_file_is_served_once_and_only_to_the_person_who_asked(
        self,
    ) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError):
            await campaign_backup.fetch_part(self.web, bob, ref, made, 1)
        await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        with self.assertRaises(RecapError):  # taken once
            await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        for n in (0, 25, 99):  # no such file
            with self.assertRaises(RecapError):
                await campaign_backup.fetch_part(self.web, self.session, ref, made, n)
        left = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertNotIn(1, [p["n"] for p in left["parts"]])  # the page sees what is still there
        self.assertEqual(left["partsTotal"], 1)

    async def test_the_copy_is_gone_once_the_main_file_and_every_part_are_fetched(self) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        before = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertTrue(before["fileWaiting"])
        await campaign_backup.fetch(self.web, self.session, ref, made)
        still = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertFalse(still["fileWaiting"])  # (so a half-finished download can resume)
        self.assertTrue(still["parts"])  # the main file alone does not end it
        await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        with self.assertRaises(RecapError):
            await campaign_backup.status(self.web, self.session, ref, made)

    async def test_the_main_file_and_its_parts_are_stored_together_or_not_at_all(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        item = await self.backups.claim_next(THURSDAY.id)
        assert item is not None
        stale = web_backup.Request(item.id, item.campaign_id, item.user_id, item.claimed_at - 1)
        self.assertFalse(await self.backups.done(THURSDAY.id, stale, b"x", "n", [b"p1"]))
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_backup_parts")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        self.assertTrue(await self.backups.done(THURSDAY.id, item, b"x", "n", [b"p1", b"p2"]))
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual([p["n"] for p in got["parts"]], [1, 2])

    async def test_a_set_with_too_many_files_fails_whole_with_nothing_kept(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        host = self.host()

        async def too_many(g: int, c: str) -> Any:
            raise BackupTooBig("too many files")

        host.export_campaign = too_many
        self.assertEqual(await self.build_next(THURSDAY.id, host), "too_big")
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual((got["state"], got["reason"]), ("failed", "too_big"))
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_backup_parts")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_the_parts_go_with_their_copy_after_fifteen_minutes_and_are_not_served_late(
        self,
    ) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        # a part is refused (and the copy no longer read) once the copy is older than 900 seconds
        await self.db_sql(THURSDAY.id, "UPDATE web_backups SET created_at = created_at - 1000")
        with self.assertRaises(RecapError):
            await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        with self.assertRaises(RecapError):
            await campaign_backup.status(self.web, self.session, ref, made)
        # ...and the sweep takes the copy and its parts, leaving no bytes behind
        later = WebBackups(self.db, clock=lambda: time.time() + web_backup.KEEP_FOR_S + 60)
        self.assertEqual(await later.sweep(THURSDAY.id), 1)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_backup_parts")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_part_is_refused_for_the_wrong_campaign_the_wrong_server_or_before_it_is_done(
        self,
    ) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        with self.assertRaises(RecapError):  # not built yet
            await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        await self.build_next(THURSDAY.id)
        with self.assertRaises(RecapError):  # another campaign's address
            await campaign_backup.fetch_part(
                self.web, self.session, self.cref(THURSDAY.id, self.two), made, 1
            )
        with self.assertRaises(RecapError):  # a server the copy is not in
            await campaign_backup.fetch_part(
                self.web, self.session, self.cref(LOST, self.lost), made, 1
            )
        got = await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)
        self.assertTrue(got[0])  # (the right address still works: nothing above spent it)

    async def test_the_door_gives_nothing_without_the_servers_scope(self) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        async with self.web.user(ALICE.id, session="x") as conn:  # no `_scoped`: no server set
            cur = await conn.execute(
                "SELECT dmbot_web_backup_take_part(%s, %s, %s, 1) AS f",
                (THURSDAY.id, self.one.id, made),
            )
            self.assertIsNone((await cur.fetchone() or {})["f"])

    async def test_a_part_that_cannot_be_stored_leaves_the_copy_unfinished(self) -> None:
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        item = await self.backups.claim_next(THURSDAY.id)
        assert item is not None
        with self.assertRaises(TypeError):  # (`len(None)` in Python, before anything is stored)
            await self.backups.done(
                THURSDAY.id,
                item,
                b"main",
                "n",
                [b"ok", None],  # type: ignore[list-item]
            )
        got = await campaign_backup.status(self.web, self.session, ref, made)
        self.assertEqual((got["state"], got["ready"], got["parts"]), ("running", False, []))

    async def test_a_copy_whose_main_file_was_taken_is_never_handed_back(self) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        await campaign_backup.fetch(self.web, self.session, ref, made)  # the main file is taken
        again = await campaign_backup.request(self.web, self.session, ref)
        self.assertNotEqual(again, made)  # (a retry must not get a set with no main file)
        # The old copy's transcripts file can still be fetched until the 15-minute sweep.
        await campaign_backup.fetch_part(self.web, self.session, ref, made, 1)

    async def test_a_second_click_before_the_main_file_is_taken_gets_the_same_copy(self) -> None:
        await self.seed_transcripts()
        ref = self.cref(THURSDAY.id, self.one)
        made = await campaign_backup.request(self.web, self.session, ref)
        await self.build_next(THURSDAY.id)
        self.assertEqual(await campaign_backup.request(self.web, self.session, ref), made)

    async def test_the_names_of_the_files_are_the_ones_discord_gives(self) -> None:
        from dmbot.ui import logic

        now = 1_700_000_000
        main = logic.backup_filename("Frostmaiden Tales", now)
        for n in (1, 2, 7):
            self.assertEqual(
                campaign_backup.part_filename(main, n, 7),
                logic.part_filename("Frostmaiden Tales", now, n, 7),
            )

    async def test_too_many_files_has_its_own_reason_and_other_sizes_stay_too_big(self) -> None:
        from dmbot.campaigns.parts import TOO_MANY_PARTS

        ref = self.cref(THURSDAY.id, self.one)
        for message, word in ((TOO_MANY_PARTS, "too_many_files"), ("a session too big", "too_big")):
            made = await campaign_backup.request(self.web, self.session, ref)
            host = self.host()

            async def refuse(g: int, c: str, message: str = message) -> Any:
                raise BackupTooBig(message)

            host.export_campaign = refuse
            self.assertEqual(await self.build_next(THURSDAY.id, host), word)
            got = await campaign_backup.status(self.web, self.session, ref, made)
            self.assertEqual((got["state"], got["reason"]), ("failed", word))
            await self.db_sql(THURSDAY.id, "DELETE FROM web_backups")

    async def test_the_website_role_cannot_read_the_parts_table_itself(self) -> None:
        import psycopg

        async with self.web.user(ALICE.id, session="x") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT * FROM web_backup_parts")

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

    async def test_the_transcripts_files_are_listed_and_downloaded_once_over_http(self) -> None:
        await self.seed_transcripts()
        base = f"/me/campaigns/{THURSDAY.id}.{self.one.id}/backup"
        made = (await self.client.post(base, headers=HEADERS)).json()["id"]
        await self.build_next(THURSDAY.id)
        status = (await self.client.get(f"{base}/{made}")).json()
        self.assertEqual(status["partsTotal"], 1)
        self.assertEqual([p["n"] for p in status["parts"]], [1])
        got = await self.client.get(f"{base}/{made}/file/1")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.headers["content-type"], "application/gzip")
        self.assertIn("transcripts-1-of-1", got.headers["content-disposition"])
        self.assertEqual(got.headers["cache-control"], "no-store")
        again = await self.client.get(f"{base}/{made}/file/1")
        self.assertEqual(again.status_code, 404)
        none = await self.client.get(f"{base}/{made}/file/9")
        self.assertEqual(none.status_code, 404)

    async def test_the_refusals_over_http(self) -> None:
        other = await self.client.post(f"/me/campaigns/{THURSDAY.id}.nope/backup", headers=HEADERS)
        self.assertEqual((other.status_code, other.json()["error"]), (404, "not_found"))
        bad = await self.client.get(f"/me/campaigns/{THURSDAY.id}.{self.one.id}/backup/zzz")
        self.assertEqual(bad.status_code, 404)


class WhileClosing(unittest.IsolatedAsyncioTestCase):
    async def test_while_closing_backups_claims_nothing(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.web_backups = SimpleNamespace(claim_next=mock.AsyncMock(return_value=None))
        b.campaigns = SimpleNamespace(guilds_with_campaigns=mock.AsyncMock(return_value=[1]))
        b._closing = True
        await b.run_web_backups()
        b.web_backups.claim_next.assert_not_awaited()
