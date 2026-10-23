"""The facts on each campaign's card on My Account (#2460), run as the website's own database role:
one read per server, only the campaigns the person runs or plays in, a quiet campaign says nothing
instead of zero, and a player's NPC count leaves out what players cannot see. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.web import me
from dmbot.web.sessions import Session
from tests.test_recap_web import BOB, Base, callers
from tests.test_web_api import ALICE, THURSDAY

NOW = 2_000_000_000


class TheFacts(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.g = THURSDAY.id
        self.c = self.one.id

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(statement, args)

    async def session_row(self, started: int, ended: int | None, lines: int = 5) -> None:
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
            " line_count) VALUES (md5(%s), %s, %s, %s, %s, %s)",
            f"{self.c}{started}", self.g, self.c, started, ended, lines,
        )  # fmt: skip

    async def add(self, name: str, role: str | None, **kw: Any) -> str:
        made = await self.memory.add_entity(
            self.g, self.c, type="character", name=name, source=DM_SOURCE, status="confirmed", **kw
        )
        if role is not None:
            await self.sql(
                "UPDATE memory_entities SET role = %s WHERE id = %s", role, made.value.id
            )
        return made.value.id

    async def facts(self, who: Session | None = None) -> dict[str, dict[str, Any]]:
        who = who or self.session
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute("SELECT dmbot_web_campaign_facts(%s) AS f", (self.g,))
            row = await cur.fetchone()
        assert row is not None
        return {str(f["id"]): f for f in (row["f"] or [])}

    async def test_a_dm_gets_the_facts_of_their_campaign_and_not_of_another(self) -> None:
        got = await self.facts()
        self.assertEqual(set(got), {self.c})  # Bob's "Two" is not Alice's
        self.assertTrue(got[self.c]["dm"])
        self.assertEqual((got[self.c]["sessions"], got[self.c]["secondsPlayed"]), (0, 0))
        self.assertFalse(got[self.c]["live"])

    async def test_sessions_and_the_time_played_count_only_what_ended(self) -> None:
        await self.session_row(1_000, 4_600)  # an hour
        await self.session_row(10_000, 11_800)  # half an hour
        got = (await self.facts())[self.c]
        self.assertEqual((got["sessions"], got["secondsPlayed"]), (2, 5_400))

    async def active(self) -> None:
        await self.sql(
            "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
            " screen_channel_id, started_by, started_at) VALUES (%s, %s, 1, 2, %s, %s)",
            self.g, self.c, ALICE.id, 1_000,
        )  # fmt: skip

    async def test_live_is_the_active_session_not_an_open_transcript_row(self) -> None:
        await self.session_row(1_000, None)  # an open row alone (a crash left it) is not live
        self.assertFalse((await self.facts())[self.c]["live"])
        await self.active()
        self.assertTrue((await self.facts())[self.c]["live"])

    async def test_players_count_claimed_player_characters(self) -> None:
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", BOB.id, mira)
        await self.add("Unclaimed", "player_character")  # nobody plays it
        self.assertEqual((await self.facts())[self.c]["players"], 1)

    async def test_a_dm_counts_every_npc_and_a_player_only_the_ones_they_may_see(self) -> None:
        await self.add("Aldric", "npc")
        secret = await self.add("The Hidden One", "npc")
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", secret
        )
        await self.add("Plain", "npc")
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", BOB.id, mira)
        self.assertEqual((await self.facts())[self.c]["npcs"], 3)  # the DM sees all three
        bob = await self.signed(BOB)
        theirs = (await self.facts(bob))[self.c]
        self.assertFalse(theirs["dm"])
        self.assertEqual(theirs["npcs"], 2)  # not the hidden one

    async def test_a_secret_named_or_audience_limited_npc_is_not_counted_for_a_player(self) -> None:
        keeper = await self.add("The Keeper", "npc")
        await self.memory.add_alias(
            self.g, self.c, keeper, "The Keeper", kind="short", source=DM_SOURCE,
            status="confirmed", secret=True,
        )  # fmt: skip
        limited = await self.add("Veiled", "npc")
        await self.sql("UPDATE memory_entities SET audience_limited = true WHERE id = %s", limited)
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", BOB.id, mira)
        bob = await self.signed(BOB)
        self.assertEqual((await self.facts(bob))[self.c]["npcs"], 0)
        self.assertEqual((await self.facts())[self.c]["npcs"], 2)  # the DM counts both

    async def test_a_server_that_is_not_in_the_session_list_gets_nothing(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g + 1),))
            cur = await conn.execute("SELECT dmbot_web_campaign_facts(%s) AS f", (self.g + 1,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["f"])

    async def test_a_dm_who_also_plays_gets_one_row(self) -> None:
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", ALICE.id, mira)
        got = await self.facts()
        self.assertEqual(list(got), [self.c])

    async def test_account_gives_a_player_one_player_card(self) -> None:
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", BOB.id, mira)
        bob = await self.signed(BOB)
        cards = (await me.build_me(self.web, bob, now=NOW))["campaigns"]
        # Bob also runs another campaign in the fixture, so look only at this one.
        self.assertEqual([c["role"] for c in cards if c["id"] == self.c], ["player"])

    async def test_account_gives_a_dm_who_also_plays_one_dm_card(self) -> None:
        mira = await self.add("Mira", "player_character")
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", ALICE.id, mira)
        cards = (await me.build_me(self.web, self.session, now=NOW))["campaigns"]
        self.assertEqual([c["id"] for c in cards], [self.c])
        self.assertNotEqual(cards[0]["role"], "player")

    async def signed(self, user: Any) -> Session:
        from dmbot.web import sessions

        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def test_someone_who_runs_or_plays_in_nothing_gets_nothing(self) -> None:
        stranger = await self.signed(SimpleNamespace(id=424242, name="Stranger", email=None))
        self.assertEqual(await self.facts(stranger), {})


class TheCardFacts(unittest.TestCase):
    def test_a_quiet_campaign_says_nothing_not_zero(self) -> None:
        got = me._facts_of({"sessions": 0, "secondsPlayed": 0, "players": 0, "npcs": 0})
        self.assertEqual(got, {})

    def test_every_fact_is_passed_on_in_the_pages_words(self) -> None:
        got = me._facts_of(
            {
                "live": True, "sessions": 4, "secondsPlayed": 9_000, "players": 3, "npcs": 12,
                "startedAt": 1_700_000_000,
            }
        )  # fmt: skip
        self.assertEqual(
            {k: got[k] for k in ("live", "sessions", "hoursPlayed", "players", "npcs")},
            {"live": True, "sessions": 4, "hoursPlayed": 2.5, "players": 3, "npcs": 12},
        )
        self.assertTrue(got["startedAt"].endswith("Z"))

    def test_a_short_session_still_has_its_hours(self) -> None:
        self.assertEqual(me._facts_of({"secondsPlayed": 300})["hoursPlayed"], 0.08)

    def test_no_facts_means_none(self) -> None:
        self.assertEqual(me._facts_of(None), {})


class TheDoor(Base):
    async def test_the_facts_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_campaign_facts("), {"schema.py", "web/me.py"})

    async def test_the_door_keeps_its_pinned_search_path_and_no_public_execute(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT proconfig::text AS cfg, prosecdef,"
                " has_function_privilege('public', oid, 'EXECUTE') AS public_may"
                " FROM pg_proc WHERE proname = 'dmbot_web_campaign_facts'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIn("search_path", row["cfg"] or "")
            self.assertTrue(row["prosecdef"])
            self.assertFalse(row["public_may"])
