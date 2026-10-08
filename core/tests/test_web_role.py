"""The website's own database role, dmbot_web (#498): it reaches only the servers in the
signed-in session's own Discord list, only the tables the API uses, and never changes the
schema. The bot's role is unaffected."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import AsyncConnection, errors
from psycopg.conninfo import make_conninfo

from dmbot import schema
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database, DatabaseError
from dmbot.web import accounts, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild, DiscordUser
from dmbot.web.me import build_me
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, TEST_URL, DatabaseTest
from tests.test_web_api import ALICE, API, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_feedback import FakeDiscussions

WEB_PASSWORD = "dmbot_web"
OUTSIDER = DiscordGuild(id=555, name="Not In Alice's List", manage=True)


async def ensure_web_role() -> None:
    """Make the role once per database cluster (only a superuser can)."""
    conn = await AsyncConnection.connect(SUPERUSER_URL, autocommit=True)
    try:
        cur = await conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (schema.WEB_ROLE,))
        if await cur.fetchone() is None:
            await conn.execute(f"CREATE ROLE {schema.WEB_ROLE} LOGIN PASSWORD '{WEB_PASSWORD}'")
    finally:
        await conn.close()


def web_url() -> str:
    return make_conninfo(TEST_URL, user=schema.WEB_ROLE, password=WEB_PASSWORD)


class WebRole(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()  # the bot's role migrates, then grants to dmbot_web
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        store = CampaignStore(self.db, clock=lambda: self.now)
        self.mine = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        self.hidden = await store.create(OUTSIDER.id, "Ashen Crown", ALICE.id)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def alice(self) -> sessions.Session:
        token = await sessions.sign_in(
            self.web, ALICE, [THURSDAY, QUILLON, GORRAK], now=self.now, days=30
        )
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        return found

    async def campaign_names(self, session: sessions.Session, guild_id: int) -> list[str]:
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild_id),))
            cur = await conn.execute("SELECT name FROM campaigns")
            return [r["name"] for r in await cur.fetchall()]

    async def test_it_sees_only_servers_in_the_sessions_own_list(self) -> None:
        session = await self.alice()
        self.assertEqual(await self.campaign_names(session, THURSDAY.id), [self.mine.name])
        # Code that sets another server (Alice is a DM there, but signed in without it in
        # her list) still sees nothing: the database holds the role to the session's list.
        self.assertEqual(await self.campaign_names(session, OUTSIDER.id), [])

    async def test_it_needs_the_person_and_their_own_session(self) -> None:
        session = await self.alice()
        async with self.web.session(session.id_hash) as conn:  # no person set
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("SELECT count(*) AS n FROM campaigns")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
        other = DiscordUser(id=2002, name="Quillon", email=None)
        token = await sessions.sign_in(self.web, other, [THURSDAY], now=self.now, days=30)
        theirs = await sessions.find(self.web, token, now=self.now)
        assert theirs is not None
        # Alice's id with someone else's session: nothing.
        async with self.web.user(ALICE.id, session=theirs.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("SELECT count(*) AS n FROM campaigns")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_me_works_through_it(self) -> None:
        session = await self.alice()
        me = await build_me(self.web, session, now=self.now)
        self.assertEqual([c["name"] for c in me["campaigns"]], [self.mine.name])

    async def test_it_can_record_an_install_only_for_a_listed_server(self) -> None:
        session = await self.alice()
        result = await accounts.record_install(
            self.web, ALICE.id, QUILLON.id, now=self.now, session=session.id_hash
        )
        self.assertEqual(result, "recorded")
        with self.assertRaises(errors.InsufficientPrivilege):  # row-level security refuses
            await accounts.record_install(
                self.web, ALICE.id, OUTSIDER.id, now=self.now, session=session.id_hash
            )

    async def test_it_cannot_touch_other_tables_or_change_the_schema(self) -> None:
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT 1 FROM consent")
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("CREATE TABLE sneaky (x INT)")
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("DELETE FROM campaigns")

    async def test_it_refuses_to_start_on_an_older_schema(self) -> None:
        newer = (*schema.MIGRATIONS, ("9999_from_the_future", "SELECT 1"))
        with self.assertRaisesRegex(DatabaseError, "Start the updated bot first"):
            await Database.open(
                web_url(), schema=self.schema, max_size=1, migrate=False, migrations=newer
            )

    async def test_the_bots_role_is_unaffected(self) -> None:
        async with self.db.guild(OUTSIDER.id) as conn:  # no person, no session
            cur = await conn.execute("SELECT name FROM campaigns")
            self.assertEqual([r["name"] for r in await cur.fetchall()], [self.hidden.name])

    async def test_its_limits_apply_to_it_and_never_to_the_bot(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT tablename, roles FROM pg_policies"
                " WHERE schemaname = current_schema() AND policyname = 'web_session_servers'"
                " ORDER BY tablename"
            )
            rows = await cur.fetchall()
            self.assertEqual(
                [(r["tablename"], list(r["roles"])) for r in rows],
                [(t, [schema.WEB_ROLE]) for t in ("campaign_dms", "campaigns", "installs")],
            )
            # The bot's plans never call the web role's functions (no cost on the live path).
            async with self.db.guild(THURSDAY.id) as bot:
                cur = await bot.execute("EXPLAIN (VERBOSE) SELECT * FROM campaigns")
                plan = " ".join(str(r["QUERY PLAN"]) for r in await cur.fetchall())
            self.assertNotIn("dmbot_web", plan)

    async def test_an_expired_session_sees_nothing(self) -> None:
        session = await self.alice()
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_sessions SET expires_at = 1 WHERE id_hash = %s",  # past, any clock
                (session.id_hash,),
            )
        self.assertEqual(await self.campaign_names(session, THURSDAY.id), [])

    async def test_campaign_dms_are_held_to_the_list_too(self) -> None:
        session = await self.alice()
        for guild, expected in ((THURSDAY.id, 1), (OUTSIDER.id, 0)):
            async with self.web.user(ALICE.id, session=session.id_hash) as conn:
                await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild),))
                cur = await conn.execute("SELECT count(*) AS n FROM campaign_dms")
                row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], expected, guild)

    async def test_it_writes_installs_only_for_the_install_server_it_manages(self) -> None:
        session = await self.alice()
        await self.joined_by_link(QUILLON.id)
        await self.joined_by_link(GORRAK.id)
        # Listed but not managed (Alice is only a member of Gorrak's Hall): refused, even
        # through the server setting rather than the install server.
        for chosen in ({"guild_id": GORRAK.id}, {"install_guild": GORRAK.id}):
            with self.assertRaises(errors.InsufficientPrivilege, msg=str(chosen)):
                async with self.web.user(ALICE.id, session=session.id_hash) as conn:
                    for name, value in chosen.items():
                        await conn.execute(
                            "SELECT set_config(%s, %s, true)", (f"dmbot.{name}", str(value))
                        )
                    await conn.execute(
                        "UPDATE installs SET installed_by_user_id = %s WHERE guild_id = %s",
                        (ALICE.id, GORRAK.id),
                    )
        # A server outside her list: its row is invisible to the role, so there's nothing
        # to link.
        await self.joined_by_link(OUTSIDER.id)
        outside = await accounts.link_install(
            self.web, ALICE.id, OUTSIDER.id, session=session.id_hash
        )
        self.assertEqual(outside, "not_installed")
        linked = await accounts.link_install(
            self.web, ALICE.id, QUILLON.id, session=session.id_hash
        )
        self.assertEqual(linked, "linked")

    async def test_your_own_install_stays_visible_after_the_server_leaves_your_list(
        self,
    ) -> None:
        session = await self.alice()
        await accounts.record_install(
            self.web, ALICE.id, QUILLON.id, now=self.now, session=session.id_hash
        )
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30)
        later = await sessions.find(self.web, token, now=self.now)
        assert later is not None
        me = await build_me(self.web, later, now=self.now)
        self.assertEqual([i["serverId"] for i in me["installs"]], [str(QUILLON.id)])

    async def test_a_member_of_the_role_is_held_too(self) -> None:
        conn = await AsyncConnection.connect(SUPERUSER_URL, autocommit=True)
        try:
            cur = await conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'dmbot_web_member'")
            if await cur.fetchone() is None:
                await conn.execute(f"CREATE ROLE dmbot_web_member IN ROLE {schema.WEB_ROLE}")
            async with conn.transaction():
                await conn.execute(f"SET LOCAL search_path = {self.schema}")
                await conn.execute("SET LOCAL ROLE dmbot_web_member")
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(OUTSIDER.id),)
                )
                cur = await conn.execute("SELECT count(*) FROM campaigns")
                row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row[0], 0)
        finally:
            await conn.close()

    async def joined_by_link(self, guild_id: int) -> None:
        async with self.db.guild(guild_id) as conn:  # the bot, as it joins
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, NULL, 0, 'link')",
                (guild_id,),
            )

    async def test_it_can_add_feedback_but_not_read_it(self) -> None:
        discussions = FakeDiscussions()
        app = create_app(settings(), self.web, FakeDiscord(), discussions=discussions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API
        ) as client:
            sent = await client.post(
                "/feedback",
                json={"kind": "question", "message": "Hi", "contact": "bel#1"},
                headers={"X-DMbot-Request": "1"},
            )
        self.assertEqual(sent.status_code, 204)
        # It may add a row (above), never read one: it has no right to.
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.web.unscoped() as conn:
                await conn.execute("SELECT count(*) FROM feedback")
        # The row is there all the same, for the database's administrator.
        admin = await AsyncConnection.connect(SUPERUSER_URL, autocommit=True)
        try:
            await admin.execute(f"SET search_path TO {self.schema}")
            found = await admin.execute("SELECT contact FROM feedback")
            self.assertEqual(await found.fetchall(), [("bel#1",)])
        finally:
            await admin.close()

    async def test_the_whole_api_works_as_the_websites_role(self) -> None:
        provider = FakeProvider(b"h" * 32, SITE)
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=provider, clock=lambda: self.now
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        ) as client:
            start = await client.get("/auth/discord/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            await client.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
            me = (await client.get("/me")).json()
            self.assertEqual([c["name"] for c in me["campaigns"]], [self.mine.name])
            body = json.dumps(
                {
                    "id": "evt-1",
                    "user_id": ALICE.id,
                    "occurred_at": self.now,
                    "kind": "subscription_started",
                    "plan": "table",
                    "period_start": self.now,
                    "period_end": self.now + 30 * 86400,
                    "subscription_id": "sub_1",
                }
            ).encode()
            hook = await client.post(
                "/webhooks/fake", content=body, headers={"x-fake-signature": provider.sign(body)}
            )
            self.assertEqual(hook.status_code, 200)
            self.assertEqual((await client.get("/me")).json()["plan"]["id"], "table")
            headers = {"X-DMbot-Request": "1"}
            # An install of hers, so deleting exercises the installs ON DELETE SET NULL.
            await self.joined_by_link(QUILLON.id)
            link = await client.post(f"/servers/{QUILLON.id}/link", headers=headers)
            self.assertEqual(link.status_code, 204)
            token = (await client.post("/account/delete/request", headers=headers)).json()
            done = await client.post("/account/delete/confirm", headers=headers, json=token)
            self.assertEqual(done.status_code, 204)
        async with self.db.guild(QUILLON.id) as conn:
            cur = await conn.execute("SELECT installed_by_user_id FROM installs")
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["installed_by_user_id"])
        self.assertEqual(await sessions.delete_expired(self.web, now=self.now + 90 * 86400), 0)
