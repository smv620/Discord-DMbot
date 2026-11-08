"""Free access for a whole server (#3788): a campaign in a listed server passes every plan gate
whoever its DM is. Made-up ids only; the real ones are never in the repository."""

from __future__ import annotations

import logging
import unittest
from typing import Any
from unittest.mock import patch

from dmbot import entitlements, usage
from dmbot.campaigns import CampaignStore
from dmbot.config import ConfigError, load_settings
from dmbot.entitlements import FREE_ACCESS, access_for, parse_free_guilds
from dmbot.web.settings import load_web_settings
from tests.pg import DatabaseTest

LISTED, OTHER, DM, FRIEND = 4040404040404, 5050505050505, 41, 42
NOW = 1_700_000_000


class TheList(unittest.TestCase):
    def tearDown(self) -> None:
        entitlements.configure_free_guilds(())

    def test_it_is_parsed_like_the_free_users(self) -> None:
        self.assertEqual(parse_free_guilds(" 11, 22 ,,"), frozenset({11, 22}))
        self.assertEqual(parse_free_guilds(""), frozenset())
        for bad in ("zzz", "1,zzz", "-5", "0", "1.5", str(2**63), "١٢٣"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                parse_free_guilds(bad)
            self.assertNotIn(bad, str(caught.exception))  # the setting is never shown

    def test_both_loaders_read_it_and_a_bad_value_refuses_to_start(self) -> None:
        bot_env = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgres://x"}
        loaded = load_settings({**bot_env, "DMBOT_FREE_GUILDS": f"{LISTED},{OTHER}"})
        self.assertEqual(loaded.free_guilds, {LISTED, OTHER})
        self.assertNotIn(str(LISTED), repr(loaded))  # never shown with the settings
        with self.assertRaises(ConfigError) as caught:
            load_settings({**bot_env, "DMBOT_FREE_GUILDS": "table"})
        self.assertIn("Fix it in .env", str(caught.exception))
        web_env = {
            "DATABASE_URL": "postgresql://x",
            "DISCORD_CLIENT_ID": "1",
            "DISCORD_CLIENT_SECRET": "s",
            "WEB_SECRET_KEY": "k" * 40,
            "WEB_SITE_URL": "https://dmbot.example/",
            "WEB_API_URL": "https://api.dmbot.example",
        }
        loaded_web = load_web_settings({**web_env, "DMBOT_FREE_GUILDS": str(LISTED)})
        self.assertEqual(loaded_web.free_guilds, {LISTED})
        self.assertNotIn(str(LISTED), repr(loaded_web))
        with self.assertRaises(ConfigError):
            load_web_settings({**web_env, "DMBOT_FREE_GUILDS": "x"})

    def test_the_ids_never_appear_in_a_log_line(self) -> None:
        with self.assertLogs("dmbot.entitlements", level=logging.INFO) as logged:
            entitlements.configure_free_guilds({LISTED, OTHER})
        text = " ".join(logged.output)
        self.assertIn("2 ids", text)
        self.assertNotIn(str(LISTED), text)
        self.assertNotIn(str(OTHER), text)

    def test_the_plain_rule_gives_free_access_for_a_listed_server_only(self) -> None:
        entitlements.configure_free_guilds({LISTED})
        here = access_for(DM, None, None, NOW, guild_id=LISTED)
        self.assertEqual((here.kind, here.plan_name, here.hours_cap), ("free", FREE_ACCESS, None))
        self.assertTrue(here.works)
        self.assertEqual(access_for(DM, None, None, NOW, guild_id=OTHER).kind, "none")
        self.assertEqual(access_for(DM, None, None, NOW).kind, "none")  # no server named


class TheGates(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_guilds, ())
        entitlements.configure_free_guilds({LISTED})
        self.campaigns = CampaignStore(self.db)

    async def test_a_dm_with_no_plan_passes_every_gate_in_a_listed_server(self) -> None:
        access = await usage.access_of(self.db, LISTED, DM, NOW)
        self.assertEqual(access.kind, "free")
        start = await usage.check_start(self.db, LISTED, DM, NOW)
        self.assertEqual(start.verdict, "ok")
        room = await usage.campaign_room(self.db, LISTED, DM, NOW)
        self.assertEqual((room.works, room.cap), (True, None))
        standing = await usage.retention_standing(self.db, LISTED, DM, NOW)
        self.assertTrue(standing.backups)

    async def test_an_unlisted_server_is_unchanged(self) -> None:
        access = await usage.access_of(self.db, OTHER, DM, NOW)
        self.assertEqual(access.kind, "none")
        self.assertEqual((await usage.check_start(self.db, OTHER, DM, NOW)).verdict, "no_plan")

    async def test_a_connection_with_no_server_or_another_one_never_gets_it(self) -> None:
        """Free access follows the transaction's own server and nothing else (#3788)."""
        entitlements.configure_free_guilds({LISTED})
        async with self.db.user(DM) as conn:  # no server set at all
            self.assertEqual((await entitlements.effective(conn, DM, NOW)).kind, "none")
        async with self.db.meter(OTHER, DM) as conn:  # another server, while only LISTED is
            self.assertEqual((await entitlements.effective(conn, DM, NOW)).kind, "none")
        async with self.db.meter(LISTED, DM) as conn:
            self.assertEqual((await entitlements.effective(conn, DM, NOW)).kind, "free")
            await conn.execute("SELECT set_config('dmbot.guild_id', '', true)")  # unset again
            self.assertEqual((await entitlements.effective(conn, DM, NOW)).kind, "none")

    async def test_the_account_page_read_knows_the_server_when_it_is_named(self) -> None:
        _plan, access = await entitlements.plan_and_access(self.db, FRIEND, NOW, guild_id=LISTED)
        self.assertEqual(access.kind, "free")
        _plan, plain = await entitlements.plan_and_access(self.db, FRIEND, NOW)
        self.assertEqual(plain.kind, "none")

    async def test_a_listed_server_tick_leaves_the_owners_plan_hours_alone(self) -> None:
        campaign = await self.campaigns.create(LISTED, "Test table", DM)
        other = await self.campaigns.create(OTHER, "Real table", DM)

        async def hours_of() -> int:
            async with self.db.meter(OTHER, DM) as conn:
                return (await usage.standing_of(conn, DM, NOW)).used_before

        before = await hours_of()
        got = await usage.add_minutes(
            self.db, guild_id=LISTED, campaign_id=campaign.id, owner_user_id=DM,
            session_started_at=NOW - 600, minutes=7, now=NOW,
        )  # fmt: skip
        self.assertEqual((got.access.kind, got.used_after, got.grace_session), ("free", 0, None))
        self.assertEqual(await hours_of(), before)  # the owner's hours did not move...
        self.assertEqual(await usage.session_minutes(self.db, LISTED, campaign.id, NOW - 600), 7)
        await usage.add_minutes(  # ...but a tick in an unlisted server still counts
            self.db, guild_id=OTHER, campaign_id=other.id, owner_user_id=DM,
            session_started_at=NOW - 600, minutes=5, now=NOW,
        )  # fmt: skip
        self.assertEqual(await hours_of(), before + 5)

    async def test_a_person_on_the_free_list_still_ticks_in_a_listed_server(self) -> None:
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users({DM})  # free as a person: recorded, as before
        campaign = await self.campaigns.create(LISTED, "Test table", DM)
        got = await usage.add_minutes(
            self.db, guild_id=LISTED, campaign_id=campaign.id, owner_user_id=DM,
            session_started_at=NOW - 600, minutes=4, now=NOW,
        )  # fmt: skip
        self.assertEqual(got.used_after, 4)

    async def test_with_no_listed_server_a_tick_is_recorded_as_before(self) -> None:
        entitlements.configure_free_guilds(())
        campaign = await self.campaigns.create(LISTED, "Test table", DM)
        got = await usage.add_minutes(
            self.db, guild_id=LISTED, campaign_id=campaign.id, owner_user_id=DM,
            session_started_at=NOW - 600, minutes=3, now=NOW,
        )  # fmt: skip
        self.assertEqual(got.used_after, 3)

    async def test_the_transcript_list_decides_per_server_for_one_owner(self) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from dmbot.web import transcripts

        # Two servers, one owner with no plan: only the listed server's campaign is shown.
        rows = {
            LISTED: [{"id": "a", "name": "A", "sessions": 1, "owner": str(DM)}],
            OTHER: [{"id": "b", "name": "B", "sessions": 1, "owner": str(DM)}],
        }
        guilds = [SimpleNamespace(id=g, name=str(g)) for g in (LISTED, OTHER)]
        scoped: list[int] = []

        async def scope(_conn: Any, guild: int) -> None:
            scoped.append(guild)

        class Conn:
            async def execute(self, _sql: str, _params: Any = ()) -> Any:
                async def fetch() -> Any:
                    return {"found": rows[scoped[-1]]}

                return SimpleNamespace(fetchone=fetch)

        class Db:
            @asynccontextmanager
            async def user(self, *_a: Any, **_k: Any) -> Any:
                yield Conn()

        session = SimpleNamespace(user_id=DM, id_hash=b"x", guilds=guilds)
        real = transcripts._plan_allows

        async def gate(_db: Any, *args: Any, **kwargs: Any) -> None:
            await real(self.db, *args, **kwargs)

        with (
            patch.object(transcripts, "_scoped", scope),
            patch.object(transcripts, "_plan_allows", gate),
        ):
            shown = await transcripts.list_campaigns(
                Db(),  # type: ignore[arg-type]
                session,  # type: ignore[arg-type]
                now=NOW,
                enforce_plans=True,
            )
        self.assertEqual([c["ref"] for c in shown], [f"{LISTED}.a"])

    async def test_the_websites_gates_follow_the_campaigns_server(self) -> None:
        from dmbot.web import rule_sharing, transcripts
        from dmbot.web.recaps import RecapError

        owner = str(DM)  # an owner with no plan at all
        await transcripts._plan_allows(self.db, owner, NOW, enforce_plans=True, guild_id=LISTED)
        with self.assertRaises(RecapError):
            await transcripts._plan_allows(self.db, owner, NOW, enforce_plans=True, guild_id=OTHER)
        with self.assertRaises(RecapError):
            await transcripts._plan_allows(self.db, owner, NOW, enforce_plans=True)
        self.assertTrue(
            await rule_sharing._can_share(self.db, owner, NOW, enforce_plans=True, guild_id=LISTED)
        )
        self.assertFalse(
            await rule_sharing._can_share(self.db, owner, NOW, enforce_plans=True, guild_id=OTHER)
        )

    async def test_the_sample_runs_paid_check_accepts_a_listed_server(self) -> None:
        from dmbot.devtools.sample_run import steps
        from dmbot.devtools.sample_run.plan import SampleError

        await steps.require_paid(self.db, DM, NOW, guild_id=LISTED)
        with self.assertRaises(SampleError):
            await steps.require_paid(self.db, DM, NOW, guild_id=OTHER)
        with self.assertRaises(SampleError):
            await steps.require_paid(self.db, DM, NOW)


if __name__ == "__main__":
    unittest.main()
