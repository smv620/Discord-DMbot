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

    async def add_login(self, discord_id: int, account: int) -> None:
        """A Discord id added to an account (0392: only the move setting opens its table)."""
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
            await conn.execute(
                "INSERT INTO account_discord_ids (discord_id, account_id, added_at)"
                " VALUES (%s, %s, 0)",
                (discord_id, account),
            )

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
        self.assertEqual(await self.refused(self.carol), "not_found")
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
        self.assertEqual(await self.refused(self.dan), "not_found")
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
        self.assertEqual(caught.exception.code, "not_found")


class WhoDoesNotCount(People):
    async def test_someone_who_only_typed_in_the_chat_is_not_one_of_the_people(self) -> None:
        # (the rule is who was heard at the table: typed lines and the DM's sidebar do not count)
        store = TranscriptStore(self.db)
        sid = await store.open_session(THURSDAY.id, self.one.id, START + 86_400)
        await store.add_lines(
            THURSDAY.id,
            sid,
            [
                Line(
                    (START + 86_400) * 1000 + 1000,
                    CAROL.id,
                    "typed words",
                    "typed words",
                    typed=True,
                )
            ],
        )
        self.assertEqual(await self.refused(self.carol), "not_found")

    async def test_someone_heard_only_at_another_campaign_is_not_one_of_the_people(self) -> None:
        store = TranscriptStore(self.db)
        sid = await store.open_session(THURSDAY.id, self.two.id, START)
        await store.add_lines(
            THURSDAY.id, sid, [Line(START * 1000 + 1000, DAN.id, "elsewhere", "elsewhere")]
        )
        self.assertEqual(await self.refused(self.dan), "not_found")

    async def test_a_character_that_is_not_confirmed_makes_no_one_a_person(self) -> None:
        waiting = await self.memory.add_entity(
            THURSDAY.id, self.one.id, type="character", name="Maybe", source=DM_SOURCE,
            status="proposed",
        )  # fmt: skip
        await self.sql(
            "UPDATE memory_entities SET played_by = %s WHERE guild_id = %s AND id = %s",
            CAROL.id, THURSDAY.id, waiting.value.id,
        )  # fmt: skip
        self.assertEqual(await self.refused(self.carol), "not_found")


class TheDoorsThemselves(People):
    """The doors that hand over words ask the rule too (#4404): a direct call cannot bypass it."""

    async def session_id(self) -> str:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, self.one.id),
            )
            return str((await cur.fetchone() or {})["id"])

    async def door(self, who: Any, call: str, *args: Any) -> Any:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            marks = ", ".join(["%s"] * (2 + len(args)))
            cur = await conn.execute(
                f"SELECT {call}({marks}) AS r", (THURSDAY.id, self.one.id, *args)
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def test_each_door_answers_nothing_to_an_outsider_and_something_to_a_person(self) -> None:
        sid = await self.session_id()
        for call, args in (
            ("dmbot_web_transcript_lines", (sid, None, None)),
            ("dmbot_web_transcript_characters", ()),
            ("dmbot_web_transcript_speakers", (sid,)),
            ("dmbot_web_transcript_choices", (sid,)),
        ):
            with self.subTest(call=call):
                self.assertIsNone(await self.door(self.carol, call, *args))
                self.assertIsNotNone(await self.door(self.bob, call, *args))  # (heard at it)

    async def test_the_stretch_door_answers_nothing_to_an_outsider_and_something_to_a_dm(
        self,
    ) -> None:
        sid = await self.session_id()
        args = (sid, BOB.id, START * 1000, START * 1000 + 5000)
        self.assertIsNone(await self.door(self.carol, "dmbot_web_transcript_stretch", *args))
        self.assertIsNotNone(await self.door(self.alice, "dmbot_web_transcript_stretch", *args))

    async def test_a_dm_and_everyone_are_let_in_by_the_lines_door(self) -> None:
        sid = await self.session_id()
        self.assertTrue(await self.door(self.alice, "dmbot_web_transcript_lines", sid, None, None))
        await self.sql(
            "UPDATE campaigns SET channels_visibility = 'everyone' WHERE guild_id = %s",
            THURSDAY.id,
        )
        self.assertTrue(await self.door(self.carol, "dmbot_web_transcript_lines", sid, None, None))

    async def test_the_session_list_stays_open_for_the_pages_that_only_number_sessions(
        self,
    ) -> None:
        # (the recap, timeline and pictures pages count sessions from it: numbers and dates only)
        self.assertIsNotNone(await self.door(self.carol, "dmbot_web_transcript_sessions"))


class TheRulesAddedIds(People):
    async def test_someone_heard_under_an_added_discord_id_is_one_of_the_people(self) -> None:
        # Dan has added a second Discord id to his account, and that id spoke at the table
        added = 5005
        await self.add_login(added, DAN.id)
        store = TranscriptStore(self.db)
        sid = await store.open_session(THURSDAY.id, self.one.id, START + 86_400)
        await store.add_lines(
            THURSDAY.id,
            sid,
            [Line((START + 86_400) * 1000 + 1000, added, "second id", "second id")],
        )
        self.assertTrue((await self.lines_of(self.dan))["lines"])
        self.assertEqual(await self.refused(self.carol), "not_found")  # (still nobody else)

    async def test_someone_added_by_hand_under_an_added_id_is_one_of_the_people(self) -> None:
        await self.add_login(5006, DAN.id)
        await self.sql(
            "INSERT INTO campaign_channel_people (guild_id, campaign_id, user_id, added_by,"
            " added_at) VALUES (%s, %s, %s, %s, 0)",
            THURSDAY.id, self.one.id, 5006, ALICE.id,
        )  # fmt: skip
        self.assertTrue((await self.lines_of(self.dan))["lines"])

    async def test_an_id_the_account_took_off_itself_counts_no_more(self) -> None:
        # (0392: it stays the account's number, but it is nobody's login)
        store = TranscriptStore(self.db)
        sid = await store.open_session(THURSDAY.id, self.one.id, START + 86_400)
        await store.add_lines(
            THURSDAY.id, sid, [Line((START + 86_400) * 1000 + 1000, DAN.id, "dan", "dan")]
        )
        self.assertTrue((await self.lines_of(self.dan))["lines"])  # (his own id spoke)
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
            await conn.execute(
                "INSERT INTO account_reserved (discord_id, account_id, removed_at)"
                " VALUES (%s, %s, 0)",
                (DAN.id, DAN.id),
            )
        self.assertEqual(await self.refused(self.dan), "not_found")


# The website doors whose body reads `transcript_lines` and need no call to the rule, each with
# the reason. Every other one must call `dmbot_web_reads_campaign` (#4404): a door added later that
# returns a transcript's words fails here until it asks the rule or is listed with its reason.
NEEDS_NO_RULE = {
    "dmbot_web_reads_campaign": "the rule itself",
    "dmbot_web_transcript_choose": "a DM's write, and the door answers a DM only",
    "dmbot_web_line_fix_add": "a line's own speaker or a DM fixes it: the door checks who",
    "dmbot_web_my_lines": "a person's own lines, only theirs",
    "dmbot_web_my_lines_delete": "a person's own lines, only theirs",
    "dmbot_web_entity_places": "the clean-up page's lines for a DM alone (0384: 'the DM's alone')",
}


class TheCatalog(People):
    async def test_every_door_that_reads_transcript_lines_asks_the_rule_or_says_why_not(
        self,
    ) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT p.proname, p.prosrc FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace"
                " AND p.proname ~ '^dmbot_web_' AND p.prosrc ~* 'transcript_lines'"
            )
            doors = {str(r["proname"]): str(r["prosrc"]) for r in await cur.fetchall()}
        self.assertTrue(doors, "no website door reads transcript_lines: the query is wrong")
        unguarded = sorted(
            name
            for name, body in doors.items()
            if "dmbot_web_reads_campaign" not in body and name not in NEEDS_NO_RULE
        )
        self.assertEqual(
            unguarded,
            [],
            "these doors read transcript_lines without asking dmbot_web_reads_campaign: ask it, or"
            " list them in NEEDS_NO_RULE with the reason",
        )

    async def test_the_list_of_exceptions_has_no_door_that_is_gone(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT p.proname FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace"
            )
            present = {str(r["proname"]) for r in await cur.fetchall()}
        self.assertEqual(sorted(set(NEEDS_NO_RULE) - present), [])
