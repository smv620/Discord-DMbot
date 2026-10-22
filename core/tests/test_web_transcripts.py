"""A campaign's session transcripts on the website (#1501), run as the website's own database role:
anyone in the server may read them (not only a DM), a person outside it gets "not found", the
cleaned version leaves out what Discord's cleaned file leaves out, the as-heard version is the raw
transcript, the download is `export.render`'s own text, and the plan rule is the bot's."""

from __future__ import annotations

import unittest.mock
from typing import Any, ClassVar

import httpx
import psycopg

from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.transcript import cleaner, export
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    VIA_VOICE,
    Line,
    Lineage,
    TranscriptSession,
)
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions, transcripts
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, API, QUILLON, THURSDAY, FakeDiscord, settings

START = 1_700_000_000
CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all


def second_session_lines(base: int) -> list[Line]:
    """Speech, off-topic chat, typed chat, a sidebar question and DMbot's answer (#935)."""
    return [
        Line(base + 1000, BOB.id, "Kazeth draws his sword", "Ka'zeth draws his sword"),
        Line(base + 2000, ALICE.id, "did you see the match", "did you see the match",
             duration_ms=5000, topic="off_topic"),
        Line(base + 3000, BOB.id, "we take a long rest", "we take a long rest", typed=True),
        Line(base + 4000, ALICE.id, "is the ferryman hostile", "is the ferryman hostile",
             sidebar=SIDEBAR_QUESTION, lineage=Lineage(ref="a1b2c3", via=VIA_VOICE)),
        Line(base + 5000, ALICE.id, "Probably not.", "Probably not.",
             sidebar=SIDEBAR_ANSWER, lineage=Lineage(reply_to="a1b2c3", model="m")),
    ]  # fmt: skip


async def read(web: Any, who: Any, ref: str, number: int, version: str, **kw: Any) -> Any:
    return await transcripts.read_session(
        web, who, ref, number, version, now=NOW, enforce_plans=kw.get("enforce_plans", False)
    )


class Transcripts(Base):
    expected_characters: ClassVar[dict[int, str]] = {}  # the labels the page should carry

    async def give_plan(self, user_id: int, plan: str) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (user_id,))
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%s, %s, 'active', 18, 0, 1, %s, %s, NULL, NULL, 0, 'fake',"
                " 'sub_1', 0, 0)",
                (user_id, plan, NOW - 100, NOW + 86400),
            )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"
        self.store_t = TranscriptStore(self.db)
        # Session 1 (older): a few lines. Session 2: speech, off-topic chat, typed chat, a sidebar
        # question and DMbot's answer. Another campaign's session too.
        first = await self.store_t.open_session(THURSDAY.id, self.one.id, START)
        await self.store_t.add_lines(
            THURSDAY.id,
            first,
            [Line(START * 1000 + 1000, ALICE.id, "we cross the river", "we cross the river")],
        )
        await self.store_t.end_session(THURSDAY.id, first, START + 3600)
        self.sid = await self.store_t.open_session(
            THURSDAY.id, self.one.id, START + 86_400, "deepgram nova-3 api.example"
        )
        await self.store_t.add_lines(
            THURSDAY.id, self.sid, second_session_lines((START + 86_400) * 1000)
        )
        await self.store_t.end_session(THURSDAY.id, self.sid, START + 90_000)
        other = await self.store_t.open_session(THURSDAY.id, self.two.id, START)
        await self.store_t.add_lines(
            THURSDAY.id, other, [Line(START * 1000 + 1000, BOB.id, "elsewhere", "elsewhere")]
        )
        self.bob = await self.session_of(BOB)
        self.carol = await self.session_of(CAROL)

    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def listed(self, who: Any, ref: str | None = None, **kw: Any) -> Any:
        found = await transcripts.list_sessions(
            self.web, who, ref or self.cref, now=NOW, enforce_plans=kw.get("enforce_plans", False)
        )
        return found["sessions"]

    # ---- who ---------------------------------------------------------------------------

    async def test_anyone_in_the_server_sees_the_list_not_only_a_dm(self) -> None:
        for who in (
            self.session,
            self.bob,
            self.carol,
        ):  # Alice DMs it, Bob DMs another, Carol none
            got = await self.listed(who)
            self.assertEqual([(s["number"], s["lines"]) for s in got], [(2, 3), (1, 1)])
        self.assertEqual((await self.listed(self.carol))[0]["startedAt"], START + 86_400)
        self.assertEqual((await self.listed(self.carol))[0]["endedAt"], START + 90_000)

    async def test_the_list_is_only_this_campaigns_sessions(self) -> None:
        got = await self.listed(self.bob, self.oref)
        self.assertEqual([(s["number"], s["lines"]) for s in got], [(1, 1)])

    async def test_someone_outside_the_server_or_a_missing_campaign_gets_not_found(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])  # signed in, but not in this server
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{THURSDAY.id}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await self.listed(who, ref)
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as caught:
            await read(self.web, elsewhere, self.cref, 2, "heard")
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_session_of_another_campaign_is_not_reachable_through_this_one(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await read(self.web, self.carol, self.oref, 2, "heard")  # "Two" has only Session 1
        self.assertEqual(caught.exception.code, "not_found")

    async def test_an_unknown_session_or_version_is_not_found(self) -> None:
        for number, version in ((9, "heard"), (0, "heard"), (2, "raw"), (2, "as-heard")):
            with self.subTest(number=number, version=version), self.assertRaises(RecapError):
                await read(self.web, self.carol, self.cref, number, version)

    async def test_the_campaign_list_has_every_campaign_with_a_transcript_in_the_persons_servers(
        self,
    ) -> None:
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=False)
        self.assertEqual(
            [(c["ref"], c["name"], c["sessions"]) for c in got],
            [(self.cref, "One", 2), (self.oref, "Two", 1)],
        )
        self.assertEqual({c["serverName"] for c in got}, {THURSDAY.name})
        elsewhere = await self.session_of(CAROL, [QUILLON])  # a server with no campaigns here
        self.assertEqual(
            await transcripts.list_campaigns(self.web, elsewhere, now=NOW, enforce_plans=False), []
        )
        # A campaign with no saved lines is not listed.
        await self.campaigns.create(THURSDAY.id, "Empty", ALICE.id)
        again = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=False)
        self.assertEqual([c["name"] for c in again], ["One", "Two"])

    async def test_the_campaign_list_follows_the_owners_plan_too(self) -> None:
        # Alice owns "One", Bob "Two". Only Bob's plan has transcripts.
        await self.give_plan(BOB.id, "table")
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=True)
        self.assertEqual([c["name"] for c in got], ["Two"])
        await self.give_plan(ALICE.id, "guild")
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=True)
        self.assertEqual([c["name"] for c in got], ["One", "Two"])

    # ---- names kept at the session (#2337) ----------------------------------------------

    async def shown_lines(self, version: str = "heard") -> Any:
        return await read(self.web, self.carol, self.cref, 2, version)

    async def test_the_names_kept_at_the_session_are_shown_and_the_dm_is_marked(self) -> None:
        await self.store_t.record_names(
            THURSDAY.id, self.sid, {BOB.id: "Bobby", ALICE.id: "Belleros"}
        )
        shown = await self.shown_lines()
        text = "\n".join(shown["lines"])
        self.assertIn("(Bobby)", text)
        self.assertIn("(Belleros, DM)", text)  # Alice DMs "One"
        self.assertNotIn("Speaker", text + "\n".join(shown["notes"]))  # no note: all are named

    async def test_someone_without_a_name_is_speaker_n_counted_among_those_without(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Bobby", ALICE.id: None})
        shown = await self.shown_lines()
        text = "\n".join(shown["lines"])
        self.assertIn("(Bobby)", text)
        self.assertIn("(Speaker 1, DM)", text)  # not found: first of those without a name
        self.assertIn(transcripts.NAMES_NOTE, shown["notes"])

    async def test_two_people_with_one_name_tell_apart(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Sam", ALICE.id: "sam"})
        text = "\n".join((await self.shown_lines())["lines"])
        self.assertIn("(Sam)", text)
        self.assertIn("(sam (2), DM)", text)

    async def test_a_real_name_that_looks_like_the_stand_in_is_no_name(self) -> None:
        await self.store_t.record_names(
            THURSDAY.id, self.sid, {BOB.id: "Speaker 7", ALICE.id: "Belleros"}
        )
        shown = await self.shown_lines()
        text = "\n".join(shown["lines"])
        self.assertIn("(Speaker 1)", text)  # numbered among the unnamed, not taken at its word
        self.assertNotIn("Speaker 7", text)
        self.assertIn(transcripts.NAMES_NOTE, shown["notes"])

    async def test_a_name_that_cleans_to_nothing_is_kept_as_not_found(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "\u200b\u200b"})
        kept = (await self.store_t.session(THURSDAY.id, self.sid)).speaker_names  # type: ignore[union-attr]
        self.assertEqual(dict(kept), {BOB.id: ""})

    async def test_nobody_can_pass_as_the_dm_by_choosing_a_name(self) -> None:
        await self.store_t.record_names(
            THURSDAY.id, self.sid, {BOB.id: "Eve, DM", ALICE.id: "Belleros"}
        )
        text = "\n".join((await self.shown_lines())["lines"])
        self.assertIn("(Eve DM)", text)
        self.assertNotIn("(Eve, DM)", text)
        self.assertIn("(Belleros, DM)", text)

    async def test_a_name_is_kept_only_for_someone_who_spoke_and_is_not_replaced(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Bobby", CAROL.id: "Carol"})
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Renamed"})
        kept = (await self.store_t.session(THURSDAY.id, self.sid)).speaker_names  # type: ignore[union-attr]
        self.assertEqual(dict(kept), {BOB.id: "Bobby"})  # Carol has no lines here

    async def test_a_person_not_found_is_kept_as_looked_for(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: None})
        unnamed = [u for sid, ids in await self.store_t.unnamed(THURSDAY.id) if sid == self.sid
                   for u in ids]  # fmt: skip
        self.assertEqual(unnamed, [ALICE.id])  # Bob was looked for; Alice never was

    async def test_deleting_a_persons_transcripts_takes_their_name_too(self) -> None:
        await self.store_t.record_names(
            THURSDAY.id, self.sid, {BOB.id: "Bobby", ALICE.id: "Belleros"}
        )
        await self.store_t.delete_person(THURSDAY.id, BOB.id)
        kept = (await self.store_t.session(THURSDAY.id, self.sid)).speaker_names  # type: ignore[union-attr]
        self.assertEqual(dict(kept), {ALICE.id: "Belleros"})

    async def test_a_transcript_deleted_by_retention_loses_its_names(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Bobby"})
        gone = await self.store_t.delete_session_transcript(
            THURSDAY.id, self.one.id, self.sid, START + 10**9, START + 10**9
        )
        self.assertGreater(gone, 0)
        kept = (await self.store_t.session(THURSDAY.id, self.sid)).speaker_names  # type: ignore[union-attr]
        self.assertEqual(dict(kept), {})

    async def test_names_go_with_the_campaign_and_are_not_another_servers(self) -> None:
        await self.store_t.record_names(THURSDAY.id, self.sid, {BOB.id: "Bobby"})
        self.assertEqual(await self.store_t.unnamed(QUILLON.id), [])  # another server sees none
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaigns WHERE id = %s", (self.one.id,))
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_sessions WHERE id = %s", (self.sid,)
            )
            self.assertEqual((await cur.fetchone())["n"], 0)  # type: ignore[index]

    async def test_the_door_answers_only_a_member_of_the_server(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await read(self.web, elsewhere, self.cref, 2, "heard")
        names, dms = await transcripts._speakers(
            self.web, elsewhere, THURSDAY.id, self.one.id, self.sid
        )
        self.assertEqual((names, dms), ({}, set()))

    # ---- what is shown -----------------------------------------------------------------

    def expected(self, version: str) -> str:
        """What export.render makes of the same rows: the website adds nothing of its own."""
        started = START + 86_400
        lines = second_session_lines(started * 1000)
        stored = TranscriptSession(
            "x", THURSDAY.id, self.one.id, started, START + 90_000, 5, (), 2,
            ("deepgram nova-3 api.example",),
        )  # fmt: skip
        names = transcripts.speaker_names(lines, version)
        return export.render(
            "One", stored, lines, names, characters=self.expected_characters,
            dms={ALICE.id}, version=version,
        )  # fmt: skip

    async def test_the_heard_version_is_the_raw_transcript_as_export_makes_it(self) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "heard")
        text = self.expected(export.AS_HEARD)
        head, _, body = text.partition("\n\n")
        self.assertEqual(shown["lines"], body.rstrip("\n").split("\n"))
        self.assertEqual(shown["notes"][:-1], head.split("\n"))
        joined = "\n".join(shown["lines"])
        self.assertIn("[DM Sidebar", joined)  # unredacted, the sidebar included
        self.assertIn("is the ferryman hostile", joined)
        self.assertIn("(typed)", joined)
        self.assertIn("did you see the match", joined)
        self.assertIn("Kazeth draws his sword", joined)  # as heard: names not fixed

    async def test_the_cleaned_version_leaves_out_what_discords_cleaned_file_leaves_out(
        self,
    ) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "cleaned")
        _head, _, body = self.expected(export.CLEANED).partition("\n\n")
        self.assertEqual(shown["lines"], body.rstrip("\n").split("\n"))
        joined = "\n".join(shown["lines"])
        self.assertIn("Ka'zeth draws his sword", joined)  # names fixed
        for hidden in ("DM Sidebar", "is the ferryman hostile", "Probably not", "(typed)",
                       "did you see the match", "long rest"):  # fmt: skip
            self.assertNotIn(hidden, joined)
        self.assertIn("skipped", joined)  # the off-topic run is a note with how long it lasted

    async def test_it_says_the_names_are_numbered_and_shows_nobodys_discord_id(self) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "heard")
        self.assertEqual(shown["notes"][-1], transcripts.NAMES_NOTE)
        everything = repr(shown)
        for private in (str(ALICE.id), str(BOB.id), "Belleros", "Bob"):
            self.assertNotIn(private, everything)
        self.assertIn("(Speaker 1)", "\n".join(shown["lines"]))
        self.assertEqual(
            transcripts.speaker_names(
                [Line(2, 7, "b", "b"), Line(1, 9, "a", "a"), Line(3, 7, "c", "c")]
            ),
            {9: "Speaker 1", 7: "Speaker 2"},
        )

    # ---- the plan rule -----------------------------------------------------------------

    async def test_the_owners_plan_decides_when_plans_are_enforced(self) -> None:
        # Alice owns "One". No plan row: no transcripts, for a player as for her.
        for who in (self.carol, self.session):
            with self.subTest(), self.assertRaises(RecapError) as caught:
                await self.listed(who, enforce_plans=True)
            self.assertEqual(caught.exception.code, "not_in_plan")
        with self.assertRaises(RecapError):
            await read(self.web, self.carol, self.cref, 2, "heard", enforce_plans=True)
        await self.give_plan(ALICE.id, "try-it")  # Try It has no copies or transcripts
        with self.assertRaises(RecapError):
            await self.listed(self.carol, enforce_plans=True)
        await self.give_plan(ALICE.id, "table")
        self.assertEqual(len(await self.listed(self.carol, enforce_plans=True)), 2)
        # The reader's own plan is nothing to do with it.
        await self.give_plan(ALICE.id, "try-it")
        await self.give_plan(CAROL.id, "guild")
        with self.assertRaises(RecapError):
            await self.listed(self.carol, enforce_plans=True)

    async def test_a_campaign_with_no_owner_has_no_plan_to_read_with(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.one.id,)
            )
        with self.assertRaises(RecapError) as caught:
            await self.listed(self.carol, enforce_plans=True)
        self.assertEqual(caught.exception.code, "not_in_plan")
        self.assertEqual(len(await self.listed(self.carol)), 2)  # plans not enforced: fine

    async def test_the_cleaned_numbering_has_no_gap_for_someone_it_hides(self) -> None:
        lines = [
            Line(1, 7, "a", "a"),
            Line(2, 8, "typed only", "typed only", typed=True),
            Line(3, 9, "b", "b"),
        ]
        self.assertEqual(
            transcripts.speaker_names(lines, export.CLEANED), {7: "Speaker 1", 9: "Speaker 2"}
        )
        self.assertEqual(
            transcripts.speaker_names(lines, export.AS_HEARD),
            {7: "Speaker 1", 8: "Speaker 2", 9: "Speaker 3"},
        )

    async def test_with_plans_not_enforced_every_member_may_read(self) -> None:
        self.assertEqual(len(await self.listed(self.carol)), 2)

    # ---- the doors and the role --------------------------------------------------------

    async def test_the_websites_role_cannot_read_the_transcript_tables(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for table in ("transcript_lines", "transcript_sessions"):
                with self.subTest(table), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"SELECT * FROM {table}")

    async def test_the_doors_answer_nothing_without_the_server_set(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_transcript_sessions(%s, %s) AS s,"
                " dmbot_web_transcript_lines(%s, %s, %s, NULL, NULL) AS l,"
                " dmbot_web_transcript_campaigns(%s) AS c,"
                " dmbot_web_transcript_characters(%s, %s) AS h",
                (
                    THURSDAY.id,
                    self.one.id,
                    THURSDAY.id,
                    self.one.id,
                    self.sid,
                    THURSDAY.id,
                    THURSDAY.id,
                    self.one.id,
                ),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["s"], row["l"], row["c"], row["h"]), (None, None, None, None))

    async def test_each_door_has_one_caller(self) -> None:
        for door in (
            "dmbot_web_transcript_sessions",
            "dmbot_web_transcript_lines",
            "dmbot_web_transcript_campaigns",
            "dmbot_web_transcript_characters",
        ):
            allowed = {"schema.py", "web/transcripts.py"}
            if door == "dmbot_web_transcript_sessions":
                # The recap pages ask it whether a session's transcript was deleted (#2022): the
                # website's role has no grant on the tables, so this door is the only way in.
                allowed.add("web/recaps.py")
            self.assertEqual(callers(door), allowed, door)


class DeletedTranscripts(Transcripts):
    """A session whose transcript was deleted (#1548) keeps its number and now stays in the list,
    marked, so the page can say so instead of showing an empty session."""

    async def delete_first(self) -> None:
        first = (await self.store_t.sessions(THURSDAY.id, self.one.id))[-1]  # the oldest
        gone = await self.store_t.delete_session_transcript(
            THURSDAY.id, self.one.id, first.id, START + 10**6, START + 5
        )
        self.assertEqual(gone, 1)

    async def test_a_deleted_session_stays_listed_with_its_number_and_time(self) -> None:
        before = await self.listed(self.carol)
        self.assertTrue(all(s["transcriptsDeletedAt"] is None for s in before))  # null otherwise
        await self.delete_first()
        got = await self.listed(self.carol)
        self.assertEqual(
            [(s["number"], s["lines"], s["transcriptsDeletedAt"]) for s in got],
            [(2, 3, None), (1, 0, START + 5)],  # the later session keeps number 2
        )

    async def test_a_campaign_with_only_deleted_transcripts_is_still_listed(self) -> None:
        await self.delete_first()
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=False)
        self.assertEqual([(c["name"], c["sessions"]) for c in got], [("One", 2), ("Two", 1)])

    async def test_a_direct_link_says_it_was_deleted_and_the_download_has_nothing(self) -> None:
        await self.delete_first()
        got = await read(self.web, self.carol, self.cref, 1, "cleaned")
        self.assertEqual(
            (got["lines"], got["next"], got["transcriptsDeletedAt"]), ([], None, START + 5)
        )
        with self.assertRaises(RecapError) as caught:
            await transcripts.render(
                self.web, self.carol, self.cref, 1, "cleaned", now=NOW, enforce_plans=False
            )
        self.assertEqual(caught.exception.code, "not_found")
        # A session that still has its lines is read as before, with no deletion time.
        other = await read(self.web, self.carol, self.cref, 2, "cleaned")
        self.assertNotIn("transcriptsDeletedAt", other)

    async def test_a_person_outside_the_server_still_learns_nothing(self) -> None:
        await self.delete_first()
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await transcripts.list_sessions(
                self.web, elsewhere, self.cref, now=NOW, enforce_plans=False
            )


class KeepDays(Transcripts):
    """How long the campaign keeps transcripts (#1575): both doors say, null for forever."""

    async def set_keep(self, days: int | None) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET transcript_keep_days = %s WHERE guild_id = %s AND id = %s",
                (days, THURSDAY.id, self.one.id),
            )

    async def test_both_doors_say_the_limit_and_null_means_forever(self) -> None:
        page = await transcripts.list_sessions(
            self.web, self.carol, self.cref, now=NOW, enforce_plans=False
        )
        self.assertIsNone(page["keepDays"])
        await self.set_keep(90)
        page = await transcripts.list_sessions(
            self.web, self.carol, self.cref, now=NOW, enforce_plans=False
        )
        self.assertEqual(page["keepDays"], 90)
        listed = await transcripts.list_campaigns(
            self.web, self.carol, now=NOW, enforce_plans=False
        )
        self.assertEqual({c["name"]: c["keepDays"] for c in listed}, {"One": 90, "Two": None})
        await self.set_keep(365)
        listed = await transcripts.list_campaigns(
            self.web, self.carol, now=NOW, enforce_plans=False
        )
        self.assertEqual({c["name"]: c["keepDays"] for c in listed}["One"], 365)

    async def test_a_person_outside_the_server_still_learns_nothing(self) -> None:
        await self.set_keep(30)
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await transcripts.list_sessions(
                self.web, elsewhere, self.cref, now=NOW, enforce_plans=False
            )
        self.assertEqual(
            await transcripts.list_campaigns(self.web, elsewhere, now=NOW, enforce_plans=False), []
        )


class Characters(Transcripts):
    """The `{character}` labels (#1512): the export's rule in a door, for any member."""

    expected_characters: ClassVar[dict[int, str]] = {BOB.id: "Dorn"}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        add = self.memory.add_entity
        g, c = THURSDAY.id, self.one.id
        kw: dict[str, Any] = {
            "source": DM_SOURCE,
            "status": "confirmed",
            "role": "player_character",
        }
        await add(g, c, type="character", name="Cerric", played_by=BOB.id, **kw)
        await add(g, c, type="character", name="Dorn", played_by=BOB.id, **kw)
        await add(g, c, type="character", name="Mira", played_by=CAROL.id, **kw)
        await add(g, c, type="character", name="Hiro", played_by=ALICE.id, **kw)
        await add(
            g, c, type="character", name="Ghost", played_by=4004, source=DM_SOURCE,
            status="proposed", role="player_character",
        )  # fmt: skip
        async with self.db.guild(g) as conn:
            # Dorn is the newer of Bob's two; Mira's own name is a secret.
            await conn.execute(
                "UPDATE memory_entities SET created_at = 10"
                " WHERE campaign_id = %s AND name = 'Cerric'",
                (c,),
            )
            await conn.execute(
                "UPDATE memory_entities SET created_at = 20"
                " WHERE campaign_id = %s AND name = 'Dorn'",
                (c,),
            )
            await conn.execute(
                "UPDATE memory_aliases SET secret = TRUE WHERE campaign_id = %s AND text = 'Mira'",
                (c,),
            )

    async def chars(self, who: Any) -> dict[int, str]:
        return await transcripts._characters(self.web, who, THURSDAY.id, self.one.id)

    async def test_newest_wins_and_a_secret_a_dm_and_a_guess_never_show(self) -> None:
        got = await self.chars(self.carol)
        self.assertEqual(got, {BOB.id: "Dorn"})  # Bob's newer character; not Mira (secret name),
        # not Hiro (Alice is a DM of the campaign), not Ghost (only a guess)

    async def test_any_member_may_ask_and_a_person_outside_the_server_gets_none(self) -> None:
        self.assertEqual(await self.chars(self.session), await self.chars(self.carol))
        elsewhere = await self.session_of(CAROL, [QUILLON])
        with self.assertRaises(RecapError):
            await transcripts.read_session(
                self.web, elsewhere, self.cref, 2, "heard", now=NOW, enforce_plans=False
            )

    async def test_a_plays_link_gives_a_person_at_the_table_their_character(self) -> None:
        g, c = THURSDAY.id, self.one.id
        vex = (
            await self.memory.add_entity(
                g, c, type="character", name="Vex", source=DM_SOURCE, status="confirmed"
            )
        ).value.id
        async with self.db.guild(g) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at, played_by) VALUES (%s, %s, %s, 'person', 'Player 5',"
                " '', 'confirmed', 'dm', 0, %s)",
                (g, c, "d" * 32, 5005),
            )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at)"
                " VALUES (%s, %s, %s, %s, 'plays', %s, '', 1, 'confirmed', 'dm', '{}', FALSE, 5)",
                (g, c, "f" * 32, "d" * 32, vex),
            )
        self.assertEqual((await self.chars(self.carol))[5005], "Vex")
        async with self.db.guild(g) as conn:  # a secret link shows nothing
            await conn.execute(
                "UPDATE memory_relations SET secret = TRUE WHERE id = %s", ("f" * 32,)
            )
        self.assertNotIn(5005, await self.chars(self.carol))

    async def test_it_agrees_with_the_bots_own_rule_for_ordinary_data(self) -> None:
        lookup = await LookupCache(self.memory).get(THURSDAY.id, self.one.id)
        theirs = {u: n for u, n in cleaner.characters(lookup).items() if u != ALICE.id}
        self.assertEqual(await self.chars(self.carol), {BOB.id: theirs[BOB.id]})

    async def test_the_labels_are_in_the_page_and_the_download_matches_the_bots_file(self) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "heard")
        self.assertTrue(any("{Dorn}" in line for line in shown["lines"]), shown["lines"])
        name, text = await transcripts.render(
            self.web, self.carol, self.cref, 2, "heard", now=NOW, enforce_plans=False
        )
        # What Discord's button builds from the same rows, names and characters.
        lines = await self.store_t.lines(THURSDAY.id, self.sid)
        stored = await self.store_t.session(THURSDAY.id, self.sid)
        assert stored is not None
        expected = export.render(
            "One", stored, lines, transcripts.speaker_names(lines, export.AS_HEARD),
            characters={BOB.id: "Dorn"}, dms={ALICE.id}, version=export.AS_HEARD,
        )  # fmt: skip
        self.assertEqual(text, expected)
        self.assertEqual(name, export.file_name("One", stored, export.AS_HEARD))


class Pages(Transcripts):
    """A session over the cap comes in pages that join exactly (#1512)."""

    async def long_session(self, count: int, ties_at: int | None = None) -> str:
        sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START + 400_000)
        base = (START + 400_000) * 1000
        lines = []
        for i in range(count):
            at = (
                base
                + (ties_at if ties_at is not None and abs(i - 5000) < 3 else i) * 1000
                + (1 if ties_at is None else 0)
            )
            lines.append(Line(at, BOB.id, f"word {i}", f"word {i}"))
        for start in range(0, count, 2000):
            await self.store_t.add_lines(THURSDAY.id, sid, lines[start : start + 2000])
        return sid

    async def page(self, sid: str, cursor: dict[str, int] | None) -> Any:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_transcript_lines(%s, %s, %s, %s, %s) AS p",
                (
                    THURSDAY.id,
                    self.one.id,
                    sid,
                    None if cursor is None else cursor["ms"],
                    None if cursor is None else cursor["id"],
                ),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["p"]

    async def test_a_session_over_the_cap_comes_in_two_pages_that_join_exactly(self) -> None:
        sid = await self.long_session(5100)
        first = await self.page(sid, None)
        self.assertEqual(len(first["lines"]), 5000)
        self.assertIsNotNone(first["next"])
        second = await self.page(sid, first["next"])
        self.assertEqual(len(second["lines"]), 100)
        self.assertIsNone(second["next"])
        joined = [x["heard"] for x in first["lines"] + second["lines"]]
        self.assertEqual(joined, [f"word {i}" for i in range(5100)])
        # The same lines the bot reads, in the same order.
        bots = [line.heard for line in await self.store_t.lines(THURSDAY.id, sid)]
        self.assertEqual(joined, bots)

    async def test_lines_at_the_same_moment_across_a_page_boundary_are_neither_lost_nor_repeated(
        self,
    ) -> None:
        sid = await self.long_session(5100, ties_at=5000)
        first = await self.page(sid, None)
        second = await self.page(sid, first["next"])
        joined = [x["heard"] for x in first["lines"] + second["lines"]]
        bots = [line.heard for line in await self.store_t.lines(THURSDAY.id, sid)]
        self.assertEqual(len(joined), 5100)
        self.assertEqual(joined, bots)

    async def test_a_short_session_is_one_page_with_no_next(self) -> None:
        page = await self.page(self.sid, None)
        self.assertIsNone(page["next"])
        self.assertEqual(len(page["lines"]), 5)

    async def test_the_download_of_a_long_session_is_one_file_equal_to_the_bots(self) -> None:
        sid = await self.long_session(5100)
        number = 3  # after the two sessions of the fixture
        _name, text = await transcripts.render(
            self.web, self.carol, self.cref, number, "heard", now=NOW, enforce_plans=False
        )
        lines = await self.store_t.lines(THURSDAY.id, sid)
        stored = await self.store_t.session(THURSDAY.id, sid)
        assert stored is not None
        expected = export.render(
            "One", stored, lines, transcripts.speaker_names(lines, export.AS_HEARD),
            dms={ALICE.id}, version=export.AS_HEARD,
        )  # fmt: skip
        self.assertEqual(text, expected)

    async def test_the_reading_page_gives_a_slice_and_where_the_next_starts(self) -> None:
        await self.long_session(2500)
        a = await transcripts.read_session(
            self.web, self.carol, self.cref, 3, "heard", now=NOW, enforce_plans=False
        )
        self.assertEqual(
            (len(a["lines"]), a["next"]), (transcripts.READ_PAGE, transcripts.READ_PAGE)
        )
        b = await transcripts.read_session(
            self.web, self.carol, self.cref, 3, "heard", now=NOW, enforce_plans=False,
            offset=a["next"],
        )  # fmt: skip
        self.assertEqual(len(b["lines"]), 500)
        self.assertIsNone(b["next"])
        self.assertEqual(a["lines"][0].split(": ")[-1], "word 0")
        self.assertEqual(b["lines"][-1].split(": ")[-1], "word 2499")

    async def test_a_session_past_the_limits_is_too_big_not_half_shown(self) -> None:
        await self.long_session(120)
        with (
            unittest.mock.patch.object(transcripts, "MAX_LINES", 100),
            self.assertRaises(RecapError) as caught,
        ):
            await transcripts.render(
                self.web, self.carol, self.cref, 3, "heard", now=NOW, enforce_plans=False
            )
        self.assertEqual(caught.exception.code, "too_big")
        with (
            unittest.mock.patch.object(transcripts, "FILE_LIMIT", 500),
            self.assertRaises(RecapError) as caught,
        ):
            await transcripts.render(
                self.web, self.carol, self.cref, 3, "heard", now=NOW, enforce_plans=False
            )
        self.assertEqual(caught.exception.code, "too_big")


class TheRoutes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store_t = TranscriptStore(self.db)
        sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START)
        await self.store_t.add_lines(
            THURSDAY.id,
            sid,
            [
                Line(START * 1000 + 1000, ALICE.id, "Kazeth draws", "Ka'zeth draws"),
                Line(
                    START * 1000 + 2000,
                    ALICE.id,
                    "off topic",
                    "off topic",
                    topic="off_topic",
                    duration_ms=4000,
                ),
            ],
        )
        await self.store_t.end_session(THURSDAY.id, sid, START + 600)
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        app = create_app(
            settings(enforce_plans=False),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, user: Any, guilds: Any) -> None:
        token = await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def test_the_list_the_read_view_and_the_download_agree(self) -> None:
        await self.sign_in(CAROL, [THURSDAY])
        listed = await self.client.get(f"/me/campaigns/{self.cref}/transcripts", headers=HEADERS)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([s["number"] for s in listed.json()["sessions"]], [1])
        self.assertEqual(listed.json()["name"], "One")
        mine = await self.client.get("/me/transcripts", headers=HEADERS)
        self.assertEqual(
            [(c["ref"], c["sessions"]) for c in mine.json()["campaigns"]], [(self.cref, 1)]
        )
        read_view = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1", params={"version": "heard"}, headers=HEADERS
        )
        self.assertEqual(read_view.status_code, 200)
        file = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1/download",
            params={"version": "heard"},
            headers=HEADERS,
        )
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(
            file.headers["content-disposition"],
            'attachment; filename="one-session-1-as-heard.txt"',
        )
        self.assertEqual(file.headers["cache-control"], "no-store")  # every signed-in answer
        body = read_view.json()
        # The download is the read view's text, joined as export.render wrote it: nothing added.
        self.assertEqual(file.text, "\n".join([*body["notes"][:-1], "", *body["lines"]]) + "\n")
        cleaned = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1/download", headers=HEADERS
        )  # the default version is the cleaned one
        self.assertIn("-cleaned.txt", cleaned.headers["content-disposition"])
        self.assertNotIn("off topic", cleaned.text)

    async def test_someone_outside_the_server_and_bad_requests_get_404(self) -> None:
        await self.sign_in(CAROL, [QUILLON])
        for path in (
            f"/me/campaigns/{self.cref}/transcripts",
            f"/me/campaigns/{self.cref}/transcripts/1",
            f"/me/campaigns/{self.cref}/transcripts/1/download",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        # The list of campaigns is simply empty for someone in no server that has one.
        self.assertEqual((await self.client.get("/me/transcripts")).json(), {"campaigns": []})
        await self.sign_in(CAROL, [THURSDAY])
        for path in (
            f"/me/campaigns/{self.cref}/transcripts/7",
            f"/me/campaigns/{self.cref}/transcripts/1?version=raw",
            f"/me/campaigns/{self.cref}/transcripts/1/download?version=raw",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        self.assertEqual(
            (await self.client.get(f"/me/campaigns/{self.cref}/transcripts")).status_code, 200
        )
