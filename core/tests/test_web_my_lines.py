"""A person's own "Delete my transcripts" on the website (#2769): their own line counts, a queued
deletion for the bot, and its progress. Never anyone else's lines, never another server's. Invented
names and ids throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from dmbot.web import my_lines, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

OTHER = DiscordGuild(id=222, name="Other Table", manage=False)
SERVER = str(THURSDAY.id)
CARL = SimpleNamespace(id=3003, name="Carl", email="carl@example.com")


class LinesBase(Base):
    """Helpers only (no tests here, so none are run twice by the classes that build on it)."""

    async def lines(
        self, user_id: int, sessions_: int = 2, per: int = 3, guild_id: int = THURSDAY.id
    ) -> None:
        """`per` lines from `user_id` in each of `sessions_` made-up sessions of a server (another
        server gets a campaign of its own for them)."""
        campaign = self.one.id
        if guild_id != THURSDAY.id:
            campaign = (await self.campaigns.create(guild_id, "Elsewhere", ALICE.id)).id
        async with self.db.guild(guild_id) as conn:
            for i in range(sessions_):
                sid = f"s-{guild_id}-{user_id}-{i}"
                await conn.execute(
                    "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (sid, guild_id, campaign, 1_000 + i + user_id),
                )
                for n in range(per):
                    await conn.execute(
                        "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                        " heard) VALUES (%s, %s, %s, %s, %s)",
                        (guild_id, sid, n, user_id, "made-up words"),
                    )

    async def signed(self, user: Any, guilds: list[DiscordGuild] | None = None) -> Any:
        token = await sessions.sign_in(self.web, user, guilds or [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def refused(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id, state, stage, lines, sessions FROM web_line_deletions"
                " ORDER BY created_at, id"
            )
            return list(await cur.fetchall())

    async def line_count(self, user_id: int, guild_id: int = THURSDAY.id) -> int:
        async with self.db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_lines WHERE user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])


class TheCounts(LinesBase):
    async def test_only_the_persons_own_lines_are_counted(self) -> None:
        await self.lines(ALICE.id, sessions_=2, per=3)
        await self.lines(BOB.id, sessions_=5, per=7)
        got = await my_lines.counts(self.web, self.session, SERVER)
        self.assertEqual((got["lines"], got["sessions"], got["open"]), (6, 2, None))

    async def test_nothing_written_down_counts_zero(self) -> None:
        got = await my_lines.counts(self.web, self.session, SERVER)
        self.assertEqual((got["lines"], got["sessions"]), (0, 0))

    async def test_lines_in_another_server_do_not_count(self) -> None:
        await self.lines(ALICE.id, guild_id=OTHER.id)
        got = await my_lines.counts(self.web, self.session, SERVER)
        self.assertEqual(got["lines"], 0)

    async def test_a_server_not_on_the_persons_list_is_not_found(self) -> None:
        self.assertEqual(
            await self.refused(my_lines.counts(self.web, self.session, "999")), "not_found"
        )
        self.assertEqual(
            await self.refused(my_lines.counts(self.web, self.session, "x1")), "not_found"
        )

    async def test_the_answer_carries_counts_and_nothing_else(self) -> None:
        await self.lines(ALICE.id)
        got = await my_lines.counts(self.web, self.session, SERVER)
        self.assertEqual(set(got), {"lines", "sessions", "open"})

    async def test_the_website_role_cannot_read_the_lines_or_the_queue_itself(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for table in ("transcript_lines", "web_line_deletions"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', %s, 'SELECT') AS can", (table,)
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertFalse(row["can"], table)


class Asking(LinesBase):
    async def test_a_deletion_is_queued_for_the_person_alone(self) -> None:
        await self.lines(ALICE.id)
        await self.lines(BOB.id)
        did = await my_lines.request(self.web, self.session, SERVER)
        self.assertRegex(did, r"^[0-9a-f]{32}$")
        (row,) = await self.rows()
        self.assertEqual(
            (row["user_id"], row["state"], row["stage"]), (ALICE.id, "waiting", "waiting")
        )
        self.assertEqual(await self.line_count(ALICE.id), 6)  # (the website deletes nothing itself)
        self.assertEqual(await self.line_count(BOB.id), 6)

    async def test_a_second_press_while_one_is_open_gets_the_same_one(self) -> None:
        await self.lines(ALICE.id)
        first = await my_lines.request(self.web, self.session, SERVER)
        second = await my_lines.request(self.web, self.session, SERVER)
        self.assertEqual(first, second)
        self.assertEqual(len(await self.rows()), 1)

    async def test_with_no_lines_there_is_nothing_to_delete(self) -> None:
        self.assertEqual(
            await self.refused(my_lines.request(self.web, self.session, SERVER)), "nothing"
        )
        self.assertEqual(await self.rows(), [])

    async def test_each_person_has_their_own_open_deletion(self) -> None:
        await self.lines(ALICE.id)
        await self.lines(CARL.id)
        carl = await self.signed(CARL)
        a = await my_lines.request(self.web, self.session, SERVER)
        c = await my_lines.request(self.web, carl, SERVER)
        self.assertNotEqual(a, c)
        self.assertEqual({r["user_id"] for r in await self.rows()}, {ALICE.id, CARL.id})

    async def test_a_server_not_on_the_list_is_not_found_and_queues_nothing(self) -> None:
        await self.lines(ALICE.id, guild_id=OTHER.id)
        self.assertEqual(
            await self.refused(my_lines.request(self.web, self.session, str(OTHER.id))), "not_found"
        )
        self.assertEqual(await self.rows(), [])

    async def test_ten_an_hour_is_the_most(self) -> None:
        await self.lines(ALICE.id)
        for _ in range(10):
            did = await my_lines.request(self.web, self.session, SERVER)
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "UPDATE web_line_deletions SET state = 'done', stage = 'done' WHERE id = %s",
                    (did,),
                )
        self.assertEqual(
            await self.refused(my_lines.request(self.web, self.session, SERVER)), "too_soon"
        )

    async def test_a_day_old_request_is_swept_and_a_new_one_can_be_made(self) -> None:
        await self.lines(ALICE.id)
        old = await my_lines.request(self.web, self.session, SERVER)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE web_line_deletions SET created_at = created_at - 90000")
        fresh = await my_lines.request(self.web, self.session, SERVER)
        self.assertNotEqual(old, fresh)
        self.assertEqual(len(await self.rows()), 1)


class TheStatus(LinesBase):
    async def start(self) -> str:
        await self.lines(ALICE.id, sessions_=2, per=3)
        return await my_lines.request(self.web, self.session, SERVER)

    async def test_a_waiting_request_reads_in_the_shared_shape(self) -> None:
        did = await self.start()
        got = await my_lines.status(self.web, self.session, SERVER, did)
        self.assertEqual(
            (got["stage"], got["state"], got["lines"], got["sessions"]),
            ("waiting", "running", None, None),
        )
        self.assertLessEqual(got["startedAt"], got["updatedAt"])

    async def test_a_finished_one_says_how_many_went(self) -> None:
        did = await self.start()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_deletions SET state = 'done', stage = 'done', lines = 6,"
                " sessions = 2 WHERE id = %s",
                (did,),
            )
        got = await my_lines.status(self.web, self.session, SERVER, did)
        self.assertEqual((got["state"], got["lines"], got["sessions"]), ("done", 6, 2))

    async def test_a_failed_one_says_so_with_a_plain_code(self) -> None:
        did = await self.start()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_deletions SET state = 'failed', stage = 'failed', error = 'failed'"
                " WHERE id = %s",
                (did,),
            )
        got = await my_lines.status(self.web, self.session, SERVER, did)
        self.assertEqual((got["state"], got["reason"]), ("failed", "failed"))

    async def test_one_that_stopped_moving_is_stalled(self) -> None:
        did = await self.start()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_deletions SET state = 'working', stage = 'deleting',"
                " updated_at = created_at WHERE id = %s",
                (did,),
            )
        later = NOW + 3600
        got = await my_lines.status(self.web, self.session, SERVER, did, now=later)
        self.assertEqual((got["stage"], got["state"]), ("deleting", "stalled"))

    async def test_only_the_person_who_asked_can_read_it(self) -> None:
        did = await self.start()
        carl = await self.signed(CARL)
        bob = await self.signed(BOB)
        self.assertEqual(
            await self.refused(my_lines.status(self.web, carl, SERVER, did)), "not_found"
        )
        self.assertEqual(
            await self.refused(my_lines.status(self.web, bob, SERVER, did)), "not_found"
        )

    async def test_another_server_and_a_bad_id_are_not_found(self) -> None:
        did = await self.start()
        other = await self.signed(ALICE, [THURSDAY, OTHER])
        self.assertEqual(
            await self.refused(my_lines.status(self.web, other, str(OTHER.id), did)), "not_found"
        )
        self.assertEqual(
            await self.refused(my_lines.status(self.web, self.session, SERVER, "nope")), "not_found"
        )


class TheRoutes(LinesBase):
    async def client(self, on: bool) -> httpx.AsyncClient:
        app = create_app(
            settings(line_delete_on=on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_count_ask_and_poll_over_http(self) -> None:
        await self.lines(ALICE.id)
        client = await self.client(True)
        got = await client.get(f"/me/transcripts/{SERVER}/mine")
        self.assertEqual(
            (got.status_code, got.json()["lines"], got.json()["sessions"]), (200, 6, 2)
        )
        made = await client.post(f"/me/transcripts/{SERVER}/delete", headers=HEADERS)
        self.assertEqual(made.status_code, 202, made.text)
        again = await client.post(f"/me/transcripts/{SERVER}/delete", headers=HEADERS)
        self.assertEqual(again.json(), made.json())
        status = await client.get(f"/me/transcripts/{SERVER}/delete/{made.json()['id']}")
        self.assertEqual((status.status_code, status.json()["stage"]), (200, "waiting"))
        open_one = await client.get(f"/me/transcripts/{SERVER}/mine")
        self.assertEqual(open_one.json()["open"]["id"], made.json()["id"])

    async def test_no_lines_is_a_conflict_named_nothing(self) -> None:
        client = await self.client(True)
        got = await client.post(f"/me/transcripts/{SERVER}/delete", headers=HEADERS)
        self.assertEqual((got.status_code, got.json()["error"]), (409, "nothing"))

    async def test_a_server_not_on_the_list_is_not_found(self) -> None:
        client = await self.client(True)
        for method, path in (
            ("GET", "/me/transcripts/999/mine"),
            ("POST", "/me/transcripts/999/delete"),
            ("GET", f"/me/transcripts/999/delete/{'a' * 32}"),
        ):
            got = await client.request(method, path, headers=HEADERS)
            self.assertEqual(got.status_code, 404, path)

    async def test_while_switched_off_every_route_says_not_found(self) -> None:
        await self.lines(ALICE.id)
        client = await self.client(False)
        for method, path in (
            ("GET", f"/me/transcripts/{SERVER}/mine"),
            ("POST", f"/me/transcripts/{SERVER}/delete"),
            ("GET", f"/me/transcripts/{SERVER}/delete/{'a' * 32}"),
        ):
            got = await client.request(method, path, headers=HEADERS)
            self.assertEqual((got.status_code, got.json()["error"]), (404, "not_found"), path)
        self.assertEqual(await self.rows(), [])
