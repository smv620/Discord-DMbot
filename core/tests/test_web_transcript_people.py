"""Who may read a campaign's transcripts on the website (#4401, the owner's ruling #3891): the
campaign's people (a DM, a confirmed character's player, someone heard at one of its sessions,
someone a DM added by hand), or anyone in the server when its channels are open to everyone.
Everyone else is refused as a stranger is. One shared database test
(`dmbot_web_reads_campaign`) is behind every page. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions, transcripts
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no part in it
DAN = type(BOB)(id=4004, name="Dan", email="dan@example.com")
START = 1_800_000_000
FAR = 10**15  # (a made-at far away, so no clock matters)


class People(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        store = TranscriptStore(self.db)
        sid = await store.open_session(
            THURSDAY.id, self.one.id, START, "deepgram nova-3 api.example"
        )
        # Alice (the DM) and Bob spoke at the table; Carol and Dan did not
        await store.add_lines(
            THURSDAY.id,
            sid,
            [
                Line(START * 1000 + 1000, ALICE.id, "we cross the river", "we cross the river"),
                Line(START * 1000 + 2000, BOB.id, "I draw my sword", "I draw my sword"),
            ],
        )
        await store.end_session(THURSDAY.id, sid, START + 3600)
        self.alice = await self.session_of(ALICE)
        self.bob = await self.session_of(BOB)
        self.carol = await self.session_of(CAROL)
        self.dan = await self.session_of(DAN)

    async def session_of(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(statement, args)

    async def lines_of(self, who: Any) -> dict[str, Any]:
        return await transcripts.read_session(
            self.web, who, self.cref, 1, "heard", now=NOW, enforce_plans=False
        )

    async def refused(self, who: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.lines_of(who)
        return caught.exception.code

    async def listed(self, who: Any) -> list[str]:
        got = await transcripts.list_campaigns(self.web, who, now=NOW, enforce_plans=False)
        return [c["ref"] for c in got]


class TheRule(People):
    async def test_a_member_outside_the_campaigns_people_is_refused_like_a_stranger(self) -> None:
        self.assertEqual(await self.refused(self.carol), "not_allowed")
        self.assertNotIn(self.cref, await self.listed(self.carol))  # (nor is it listed to them)

    async def test_the_dm_may_read(self) -> None:
        got = await self.lines_of(self.alice)
        self.assertTrue(got["lines"])
        self.assertIn(self.cref, await self.listed(self.alice))

    async def test_someone_heard_at_a_session_may_read(self) -> None:
        got = await self.lines_of(self.bob)  # (Bob spoke at the table)
        self.assertTrue(got["lines"])
        self.assertIn(self.cref, await self.listed(self.bob))

    async def test_a_confirmed_characters_player_may_read(self) -> None:
        hero = await self.memory.add_entity(
            THURSDAY.id, self.one.id, type="character", name="Hero", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        await self.sql(
            "UPDATE memory_entities SET played_by = %s WHERE guild_id = %s AND id = %s",
            CAROL.id, THURSDAY.id, hero.value.id,
        )  # fmt: skip
        self.assertTrue((await self.lines_of(self.carol))["lines"])

    async def test_someone_a_dm_added_by_hand_may_read(self) -> None:
        self.assertEqual(await self.refused(self.dan), "not_allowed")
        await self.sql(
            "INSERT INTO campaign_channel_people (guild_id, campaign_id, user_id, added_by,"
            " added_at) VALUES (%s, %s, %s, %s, 0)",
            THURSDAY.id, self.one.id, DAN.id, ALICE.id,
        )  # fmt: skip
        self.assertTrue((await self.lines_of(self.dan))["lines"])

    async def test_with_everyone_any_member_may_read(self) -> None:
        await self.sql(
            "UPDATE campaigns SET channels_visibility = 'everyone' WHERE guild_id = %s AND id = %s",
            THURSDAY.id, self.one.id,
        )  # fmt: skip
        self.assertTrue((await self.lines_of(self.carol))["lines"])
        self.assertIn(self.cref, await self.listed(self.carol))

    async def test_a_session_list_and_the_choices_follow_the_same_rule(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await transcripts.list_sessions(
                self.web, self.carol, self.cref, now=NOW, enforce_plans=False
            )
        self.assertEqual(caught.exception.code, "not_allowed")
