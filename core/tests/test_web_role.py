"""The website's own database role, dmbot_web (#498): it reaches only the servers in the
signed-in session's own Discord list, only the tables the API uses, and never changes the
schema. The bot's role is unaffected."""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import AsyncConnection, errors
from psycopg.conninfo import make_conninfo

from dmbot import schema
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database, DatabaseError, drop_schema
from dmbot.web import accounts, feedback, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild, DiscordUser
from dmbot.web.me import build_me
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, TEST_URL, DatabaseTest, create_role_sql
from tests.test_web_api import ALICE, API, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_feedback import FakeDiscussions

WEB_PASSWORD = "dmbot_web"
OUTSIDER = DiscordGuild(id=555, name="Not In Alice's List", manage=True)
OTHER_USER = 424242  # made-up: the person offered, and "someone else" as a sender


async def ensure_web_role() -> None:
    """Make the role once per database cluster (only a superuser can)."""
    conn = await AsyncConnection.connect(SUPERUSER_URL, autocommit=True)
    try:
        cur = await conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (schema.WEB_ROLE,))
        if await cur.fetchone() is None:
            # Another test worker may make it between the check and here (pytest -n 4).
            await conn.execute(create_role_sql(schema.WEB_ROLE, WEB_PASSWORD))
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

    async def test_an_older_schema_grants_only_the_tables_it_has(self) -> None:
        # A database brought up to an older migration (as a backfill test does) mustn't
        # fail on rights for tables a later migration makes; they come with that one.
        await self.web.close()
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        before = [m for m in schema.MIGRATIONS if m[0] < "0026"]
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        self.assertIn("0026_feedback", await self.db.migrate())
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.assertEqual(await feedback.forget_old(self.web), 0)  # its rights arrived

    async def test_sheets_are_not_open_before_the_rule_that_holds_them_exists(self) -> None:
        # A database at migration 0028 has the sheets table but not its player column, so the
        # own-rows policy can't be made yet: the role gets no right to the table either (#1150).
        await self.web.close()
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        before = [m for m in schema.MIGRATIONS if m[0] < "0029"]
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        # Checked against the same older list: the website's connection refuses a schema that
        # is older than the code it is told to expect.
        self.web = await Database.open(
            web_url(), schema=self.schema, max_size=2, migrate=False, migrations=before
        )
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT 1 FROM character_sheets")

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
                await conn.execute("SELECT 1 FROM transcript_lines")  # consent is open since #1150
        async with self.web.unscoped() as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT 1 FROM house_rules")  # #865: the bot's only
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

    async def test_the_pause_policies_are_not_open_to_it(self) -> None:
        # #957: the setting that opens `campaigns` for the pause function is one any session
        # can set, so the policies it opens must be for the bot's role only.
        session = await self.alice()
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.owner_sync', 'pause', true)")
            cur = await conn.execute("SELECT name FROM campaigns")  # no server chosen
            self.assertEqual(await cur.fetchall(), [])
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.owner_sync', 'pause', true)")
            with self.assertRaises(errors.InsufficientPrivilege):  # no grant on the column
                await conn.execute("UPDATE campaigns SET paused = TRUE")
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT policyname, roles, cmd FROM pg_policies WHERE schemaname ="
                " current_schema() AND policyname IN ('pause_read', 'pause_by_owner')"
                " ORDER BY policyname"
            )
            rows = await cur.fetchall()
        self.assertEqual(
            [(r["policyname"], r["cmd"]) for r in rows],
            [("pause_by_owner", "UPDATE"), ("pause_read", "SELECT")],
        )
        for row in rows:
            self.assertNotIn(schema.WEB_ROLE, list(row["roles"]))
            self.assertNotIn("public", list(row["roles"]))

    async def test_the_orphan_policies_are_not_open_to_it(self) -> None:
        # #1059: like the pause ones, the setting that opens `campaigns` to the orphan function
        # is one any session can set, so the policies must be for the migration role only.
        session = await self.alice()
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.owner_sync', 'orphan', true)")
            cur = await conn.execute("SELECT name FROM campaigns")  # no server chosen
            self.assertEqual(await cur.fetchall(), [])
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT policyname, roles FROM pg_policies WHERE schemaname ="
                " current_schema() AND policyname IN ('orphan_read', 'orphan_update')"
            )
            rows = await cur.fetchall()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn(schema.WEB_ROLE, list(row["roles"]))
            self.assertNotIn("public", list(row["roles"]))

    async def test_the_hours_door_gives_only_ones_own_minutes_and_the_table_stays_closed(
        self,
    ) -> None:
        # #824: the account page's hours come through one function, not the table.
        for owner, minutes in ((ALICE.id, 95), (OTHER_USER, 400)):
            async with self.db.meter(THURSDAY.id, owner) as conn:
                await conn.execute(
                    "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                    " VALUES (%s, 1000, %s)",
                    (owner, minutes),
                )
        session = await self.alice()
        async with self.web.user(ALICE.id, session=session.id_hash) as conn:
            cur = await conn.execute("SELECT dmbot_hours_used(1000) AS n")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 95)  # hers, never the other person's 400
            cur = await conn.execute("SELECT dmbot_hours_used(2000) AS n")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # a month with nothing recorded
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.web.user(ALICE.id, session=session.id_hash) as conn:
                await conn.execute("SELECT * FROM owner_hours")

    async def _grant(self, privilege: str) -> None:
        """Give the website's role `privilege` on the offers table for this test's schema (only
        a superuser can; the schema is dropped afterwards, so nothing carries over)."""
        admin = await AsyncConnection.connect(SUPERUSER_URL, autocommit=True)
        try:
            table = f"{self.schema}.campaign_handover_offers"
            await admin.execute(f"GRANT {privilege} ON {table} TO {schema.WEB_ROLE}")
            await admin.execute(f"GRANT USAGE ON SEQUENCE {table}_id_seq TO {schema.WEB_ROLE}")
        finally:
            await admin.close()

    async def _offer_insert(self, conn: Any, from_user: int, **extra: object) -> None:
        """Insert an offer by email for Alice's campaign as the website's role would (#2532):
        addressed to nobody, holding a link's hash, born delivered."""
        await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
        made = int(time.time())  # (the rule holds the time to the database's clock)
        columns = {
            "guild_id": THURSDAY.id,
            "campaign_id": self.mine.id,
            "from_user_id": from_user,
            "to_user_id": 0,
            "created_at": made,
            "delivered_at": made,
            "from_name": "Alice",
            "to_name": "",
            "via_email": True,
            "token_hash": "a" * 64,
            **extra,
        }
        names = ", ".join(columns)
        marks = ", ".join(["%s"] * len(columns))
        await conn.execute(
            f"INSERT INTO campaign_handover_offers ({names}) VALUES ({marks})",
            tuple(columns.values()),
        )

    async def refused(self, session: Any, who: int = ALICE.id, **extra: object) -> None:
        with self.assertRaises(errors.InsufficientPrivilege, msg=str(extra)):
            async with self.web.user(who, session=session.id_hash) as conn:
                await self._offer_insert(conn, who, **extra)

    async def test_the_default_grant_lets_the_owner_make_an_email_offer(self) -> None:
        session = await self.alice()
        async with self.web.user(ALICE.id, session=session.id_hash) as conn:
            await self._offer_insert(conn, ALICE.id)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM campaign_handover_offers")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)

    async def test_the_owner_creates_an_offer_by_email_and_nothing_else(self) -> None:
        # The grant is column-limited and a restrictive rule backs it (#728, #2532).
        columns = ", ".join(schema.OFFER_INSERT_COLUMNS)
        await self._grant(f"INSERT ({columns})")
        session = await self.alice()
        async with self.web.user(ALICE.id, session=session.id_hash) as conn:
            await self._offer_insert(conn, ALICE.id)
        for column in ("claimed_at", "message_id", "end_told_at", "status"):  # no grant
            await self.refused(session, **{column: 1})
        await self.refused(session, who=ALICE.id, from_user_id=OTHER_USER)  # not hers to name

    async def test_a_discord_offer_cannot_come_from_the_website(self) -> None:
        # PLAN point 6: no new Discord offers; the bot would send a person buttons for one.
        await self._grant("INSERT")
        session = await self.alice()
        await self.refused(session, via_email=False, token_hash=None, to_user_id=OTHER_USER,
                           delivered_at=None)  # fmt: skip
        await self.refused(session, via_email=False, to_user_id=0)

    async def test_an_offer_can_only_be_made_on_a_campaign_the_sender_owns(self) -> None:
        # #2532: anyone in the server could otherwise write themselves an offer and accept it.
        columns = ", ".join(schema.OFFER_INSERT_COLUMNS)
        await self._grant(f"INSERT ({columns})")
        bobs = await CampaignStore(self.db, clock=lambda: self.now).create(
            THURSDAY.id, "Not hers", OTHER_USER
        )
        await self.refused(await self.alice(), campaign_id=bobs.id, to_user_id=ALICE.id)
        await self.refused(await self.alice(), campaign_id=bobs.id)

    async def test_an_email_offer_holds_no_one_and_carries_a_link_hash(self) -> None:
        columns = ", ".join(schema.OFFER_INSERT_COLUMNS)
        await self._grant(f"INSERT ({columns})")
        session = await self.alice()
        now = int(time.time())
        for bad in (
            {"to_user_id": ALICE.id},  # already addressed to someone
            {"token_hash": None},  # no link
            {"delivered_at": now + 1},  # "delivered" at some other time
            {"delivered_at": None},
            {"created_at": now + 10_000_000, "delivered_at": now + 10_000_000},  # never expires
            {"created_at": now - 100_000, "delivered_at": now - 100_000},  # born expired
        ):
            await self.refused(session, **bad)

    async def test_a_wider_grant_is_still_held_by_the_rule(self) -> None:
        # If someone granted INSERT on the whole table by mistake, the rule still holds (#728).
        await self._grant("INSERT")
        session = await self.alice()
        for column in ("claimed_at", "message_id", "end_told_at"):
            await self.refused(session, **{column: 1})
        await self.refused(session, status="accepted")

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
                [
                    (t, [schema.WEB_ROLE])
                    for t in ("campaign_dms", "campaign_handover_offers", "campaigns", "installs")
                ],
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
                # Or another test worker made it meanwhile (pytest -n 4).
                with contextlib.suppress(errors.DuplicateObject):
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
        self.assertEqual(sent.status_code, 200)
        # Its yearly sweep runs, and leaves this new row alone.
        self.assertEqual(await feedback.forget_old(self.web), 0)
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
        # Her campaigns stay, ownerless, in every server, even one outside her session's list
        # (#1059): the database function is the one door across servers.
        for guild_id, campaign_id in ((THURSDAY.id, self.mine.id), (OUTSIDER.id, self.hidden.id)):
            async with self.db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "SELECT owner_user_id, owner_left_at FROM campaigns WHERE id = %s",
                    (campaign_id,),
                )
                kept = await cur.fetchone()
            assert kept is not None
            self.assertIsNone(kept["owner_user_id"])
            self.assertEqual(kept["owner_left_at"], self.now)
