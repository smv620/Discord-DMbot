"""The DM's recap page (#1382), run as the website's own database role: only a DM of the
campaign sees or changes a recap; edits, Post, Try again and Don't post are checked in the
database, not just the route; the address door answers only while it is open; and the bot's
Post step re-checks the plan, the chat channel, secret names and who is still recorded."""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import psycopg

from dmbot.campaigns.store import CampaignStore
from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.recaps import notify
from dmbot.recaps.approval import text_hash
from dmbot.recaps.audio.files import KINDS, AudioFiles
from dmbot.recaps.draft import SUMMARY_MAX
from dmbot.recaps.hold import HoldStore
from dmbot.recaps.store import Job, RecapStore
from dmbot.transcript.store import TranscriptStore
from dmbot.web import recap_audio, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import (
    RecapError,
    audio_again,
    decide,
    edit_recap,
    list_recaps,
    month_usage,
    read_recap,
)
from tests import recap_fixtures as fx
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}
BOB = SimpleNamespace(id=2002, name="Bob", email="bob@example.com")
NOW = int(time.time())


def callers(needle: str) -> set[str]:
    """Which source files mention this: a new caller of a door fails here and must be looked at."""
    root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    return {
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if needle in f.read_text(encoding="utf-8")
    }


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.campaigns = CampaignStore(self.db)
        # Alice DMs "One"; Bob DMs "Two" in the same server.
        self.one = await self.campaigns.create(THURSDAY.id, "One", ALICE.id)
        self.two = await self.campaigns.create(THURSDAY.id, "Two", BOB.id)
        self.store = RecapStore(self.db)
        for campaign, dm in ((self.one, ALICE.id), (self.two, BOB.id)):
            await self.store.enqueue(THURSDAY.id, campaign.id, 100, dm_user_id=dm)
            await self.store.update(
                THURSDAY.id, campaign.id, 100, state="ready", title="The Silver Lock",
                summary=fx.SUMMARY, original=fx.SUMMARY, housekeeping=["Mira drew her blade."],
                music=["m"], speakers=[fx.ALICE],
            )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        self.session = found
        self.ref = notify.job_ref(THURSDAY.id, self.one.id, 100)
        self.other = notify.job_ref(THURSDAY.id, self.two.id, 100)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def state(self, campaign: str) -> Job:
        job = await self.store.get(THURSDAY.id, campaign, 100)
        assert job is not None
        return job


class TheHeldRecaps(Base):
    """A recap waiting for the DM's name answers has no job yet, so the list and the page say it
    from the hold alone (#2174): `waitingForNames` and `namesToAnswer`, for the DM only."""

    async def hold(
        self, campaign: str, at: int = 200, *, asked: bool = True, names: int = 3
    ) -> None:
        made = await HoldStore(self.db).hold(
            THURSDAY.id, campaign, at, session_id="s1", dm_user_id=ALICE.id, channel_id=5,
            asked=asked, names_open=names,
        )  # fmt: skip
        self.assertTrue(made)  # (a hold is never written for a session that has a job)

    async def test_a_held_recap_is_listed_and_readable_with_how_many_answers_it_waits_on(
        self,
    ) -> None:
        await self.hold(self.one.id)
        listed = await list_recaps(self.web, self.session)
        by_at = {r["sessionAt"]: r for r in listed}
        held, real = by_at[200], by_at[100]
        self.assertEqual((held["waitingForNames"], held["namesToAnswer"]), (True, 3))
        self.assertEqual((real["waitingForNames"], real["namesToAnswer"]), (False, 0))
        self.assertEqual(held["campaign"], "One")
        self.assertFalse(held["canEdit"] or held["canPost"] or held["canDrop"])
        page = await read_recap(self.web, self.session, held["ref"])
        self.assertEqual((page["waitingForNames"], page["namesToAnswer"]), (True, 3))
        self.assertEqual(page["ref"], held["ref"])

    async def test_only_the_campaigns_dm_sees_it_and_a_silent_hold_is_not_shown(self) -> None:
        await self.hold(self.two.id)  # Bob's campaign: Alice is not its DM
        await self.hold(self.one.id, 300, asked=False)  # waiting for the extractor: nothing to say
        listed = await list_recaps(self.web, self.session)
        self.assertEqual(sorted(r["sessionAt"] for r in listed), [100])
        with self.assertRaises(RecapError):
            await read_recap(self.web, self.session, notify.job_ref(THURSDAY.id, self.two.id, 200))
        with self.assertRaises(RecapError):
            await read_recap(self.web, self.session, notify.job_ref(THURSDAY.id, self.one.id, 300))

    async def test_a_released_hold_shows_its_job_and_not_a_second_entry(self) -> None:
        await self.hold(self.one.id)
        holds = HoldStore(self.db)
        self.assertEqual(await holds.release(THURSDAY.id, self.one.id, 200), "queued")
        listed = await list_recaps(self.web, self.session)
        mine = [r for r in listed if r["sessionAt"] == 200]
        self.assertEqual([(r["waitingForNames"], r["state"]) for r in mine], [(False, "queued")])

    async def test_the_door_answers_nothing_for_a_server_that_is_not_the_sessions(self) -> None:
        await self.hold(self.one.id)
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", ("999",))
            cur = await conn.execute("SELECT dmbot_web_recap_holds(%s) AS r", (THURSDAY.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["r"], [])  # the server set is not the one asked about
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("SELECT dmbot_web_recap_holds(%s) AS r", (THURSDAY.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual([h["namesOpen"] for h in row["r"]], [3])
            self.assertEqual(set(row["r"][0]), {"campaignId", "campaign", "sessionAt", "namesOpen"})

    async def test_a_job_in_another_campaign_with_the_same_time_does_not_hide_a_hold(self) -> None:
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)
        await self.store.enqueue(THURSDAY.id, three.id, 400, dm_user_id=ALICE.id)
        await self.hold(self.one.id, 400)  # One waits at the very time Three has a recap
        listed = await list_recaps(self.web, self.session)
        at_400 = sorted(
            (r["campaign"], r["waitingForNames"]) for r in listed if r["sessionAt"] == 400
        )
        self.assertEqual(at_400, [("One", True), ("Three", False)])

    async def test_a_hold_and_a_job_for_the_same_session_are_listed_once(self) -> None:
        await self.hold(self.one.id, 300)  # held first ...
        await self.store.enqueue(
            THURSDAY.id, self.one.id, 300, dm_user_id=ALICE.id
        )  # ... then a job
        listed = await list_recaps(self.web, self.session)
        mine = [r for r in listed if r["campaign"] == "One" and r["sessionAt"] == 300]
        self.assertEqual([(r["waitingForNames"], r["state"]) for r in mine], [(False, "queued")])

    async def test_a_hold_with_nothing_left_to_answer_is_not_shown_as_waiting(self) -> None:
        await self.hold(self.one.id, names=0)  # answered; the next round releases it
        listed = await list_recaps(self.web, self.session)
        self.assertEqual([r["sessionAt"] for r in listed], [100])

    async def test_the_door_refuses_a_server_set_right_but_not_on_the_sessions_list(self) -> None:
        other = 5_150_000_000_000_001
        away = await self.campaigns.create(other, "Away", ALICE.id)  # Alice DMs it, off her list
        await HoldStore(self.db).hold(
            other, away.id, 200, session_id="s", dm_user_id=ALICE.id, channel_id=5, names_open=2
        )
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(other),))
            cur = await conn.execute("SELECT dmbot_web_recap_holds(%s) AS r", (other,))
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"], [])

    async def test_the_website_cannot_read_the_holds_table_itself(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT count(*) FROM recap_holds")


class TheDmsPage(Base):
    async def test_a_dm_sees_only_their_own_campaigns_recaps(self) -> None:
        listed = await list_recaps(self.web, self.session)
        self.assertEqual([r["ref"] for r in listed], [self.ref])
        self.assertEqual(listed[0]["campaign"], "One")
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(one["title"], "The Silver Lock")
        self.assertNotIn("《", one["text"])  # the cues are hidden on the page
        self.assertIn("《Tense》", one["summary"])  # and kept for the edit box
        self.assertTrue(one["canPost"] and one["canEdit"] and one["canTryAgain"])
        self.assertEqual(one["triesLeft"], 2)

    async def test_the_names_to_confirm_are_on_the_dms_page_only(self) -> None:
        """(#2027) Names the recap kept that DMbot did not know: the DM's own copy lists them;
        they are never part of the text that is edited or posted."""
        await self.store.update(THURSDAY.id, self.one.id, 100, to_confirm=["Zarathon Keep", "Yrsa"])
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(one["toConfirm"], ["Zarathon Keep", "Yrsa"])
        self.assertNotIn("Zarathon", one["summary"] + one["text"])
        listed = await list_recaps(self.web, self.session)
        self.assertEqual(listed[0]["toConfirm"], ["Zarathon Keep", "Yrsa"])

    async def test_the_critics_notes_are_on_the_dms_page_only(self) -> None:
        """(#2255) What the critics still asked when they could not agree is the DM's copy: never
        part of the text that is edited or posted, and never on a shared link."""
        from dmbot.recaps.critics import Loop

        note = "the style: keep the stakes and the mood"
        await self.store.update(
            THURSDAY.id, self.one.id, 100, critics=Loop(round=4, done=True, notes=(note,)).to_json()
        )
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(one["criticNotes"], [note])
        self.assertNotIn("stakes", one["summary"] + one["text"])
        listed = await list_recaps(self.web, self.session)
        self.assertEqual(listed[0]["criticNotes"], [note])

    async def test_a_recap_waiting_for_its_last_check_says_so_and_is_not_a_failure(self) -> None:
        from dmbot.recaps.critics import CHECK_WAIT

        await self.store.update(THURSDAY.id, self.one.id, 100, state="prose", last_error=CHECK_WAIT)
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(
            (one["waitingForCheck"], one["working"], one["error"]), (True, True, CHECK_WAIT)
        )
        await self.store.update(THURSDAY.id, self.one.id, 100, state="ready", last_error="")
        again = await read_recap(self.web, self.session, self.ref)
        self.assertFalse(again["waitingForCheck"])

    async def test_a_recap_unchecked_for_days_exposes_since_when_and_the_marked_sentences(
        self,
    ) -> None:
        from dmbot.recaps.critics import CHECK_WAIT, Loop

        await self.store.update(
            THURSDAY.id, self.one.id, 100, state="prose", last_error=CHECK_WAIT,
            critics=Loop(waits=3, since=1234).to_json(),
        )  # fmt: skip
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(one["uncheckedSince"], 1234)
        self.assertIsInstance(one["uncheckedSentences"], list)
        # (long past 3 days) the draft is given to read, in the sentences the numbers count by
        from dmbot.recaps import draft
        from dmbot.recaps.text import strip_cues

        self.assertEqual(one["uncheckedDraft"], draft.sentences_of(strip_cues(one["summary"])))
        self.assertTrue(
            all(1 <= n <= len(one["uncheckedDraft"]) for n in one["uncheckedSentences"])
        )
        self.assertFalse(one["canApproveUnchecked"])  # the approve is not on until the bot has it
        await self.store.update(THURSDAY.id, self.one.id, 100, state="ready", last_error="")
        again = await read_recap(self.web, self.session, self.ref)
        self.assertEqual((again["uncheckedSince"], again["uncheckedSentences"]), (0, []))
        self.assertEqual(again["uncheckedDraft"], [])

    async def test_the_draft_is_given_only_after_exactly_3_days(self) -> None:
        import time

        from dmbot.recaps.critics import CHECK_WAIT, UNCHECKED_AFTER_S, Loop

        for age, given in ((UNCHECKED_AFTER_S - 3600, False), (UNCHECKED_AFTER_S + 3600, True)):
            await self.store.update(
                THURSDAY.id, self.one.id, 100, state="prose", last_error=CHECK_WAIT,
                critics=Loop(waits=2, since=int(time.time()) - age).to_json(),
            )  # fmt: skip
            one = await read_recap(self.web, self.session, self.ref)
            self.assertEqual(bool(one["uncheckedDraft"]), given, age)

    async def test_a_failed_recap_is_not_offered_to_be_opened_unchecked(self) -> None:
        from dmbot.recaps.critics import Loop

        await self.store.update(
            THURSDAY.id, self.one.id, 100, state="failed", last_error="x",
            critics=Loop(waits=3, since=1234).to_json(),
        )  # fmt: skip
        one = await read_recap(self.web, self.session, self.ref)
        self.assertFalse(one["canApproveUnchecked"])

    async def test_a_recap_that_has_waited_less_than_3_days_gives_no_draft_to_read(self) -> None:
        import time

        from dmbot.recaps.critics import CHECK_WAIT, Loop

        await self.store.update(
            THURSDAY.id, self.one.id, 100, state="prose", last_error=CHECK_WAIT,
            critics=Loop(waits=2, since=int(time.time()) - 86400).to_json(),
        )  # fmt: skip
        one = await read_recap(self.web, self.session, self.ref)
        self.assertGreater(one["uncheckedSince"], 0)
        self.assertEqual(one["uncheckedDraft"], [])
        self.assertFalse(one["canApproveUnchecked"])

    async def test_a_recap_says_when_its_sessions_transcript_was_deleted(self) -> None:
        """(#2022) The recap stays; its page says the transcript went, and when, and how long the
        table keeps them. Read through the transcripts page's own door, scoped to the recap's
        server and its own campaign: a second campaign Alice also DMs, with a session at the same
        time, is not marked by the first one's deletion."""
        transcripts = TranscriptStore(self.db)
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)
        await self.store.enqueue(THURSDAY.id, three.id, 100, dm_user_id=ALICE.id)
        await self.store.update(
            THURSDAY.id, three.id, 100, state="ready", title="The Quiet Door",
            summary=fx.SUMMARY, original=fx.SUMMARY, housekeeping=[], music=["m"],
            speakers=[fx.ALICE],
        )  # fmt: skip
        # Each campaign's session started at 100, the time its recap carries; "Two" has one too.
        sessions_of = {}
        for campaign in (self.one, self.two, three):
            sid = await transcripts.open_session(THURSDAY.id, campaign.id, 100)
            await transcripts.end_session(THURSDAY.id, sid, 200)
            sessions_of[campaign.id] = sid
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET transcript_keep_days = 90 WHERE guild_id = %s",
                (THURSDAY.id,),
            )

        def marks(listed: list[dict[str, Any]]) -> dict[str, Any]:
            return {r["campaign"]: (r["transcriptsDeletedAt"], r["keepDays"]) for r in listed}

        # Nothing deleted: every recap says null, with no limit (the limit is only given with a
        # deletion), though the table keeps transcripts for 90 days.
        self.assertEqual(
            marks(await list_recaps(self.web, self.session)),
            {"One": (None, None), "Three": (None, None)},
        )
        # Another DM's campaign going first changes nothing here, and a deletion in "One" does
        # not mark "Three", which has a session at the same time.
        await transcripts.delete_session_transcript(
            THURSDAY.id, self.two.id, sessions_of[self.two.id], 10**6, 400
        )
        await transcripts.delete_session_transcript(
            THURSDAY.id, self.one.id, sessions_of[self.one.id], 10**6, 300
        )
        listed = await list_recaps(self.web, self.session)
        self.assertEqual(marks(listed), {"One": (300, 90), "Three": (None, None)})
        read = await read_recap(self.web, self.session, self.ref)
        self.assertEqual((read["transcriptsDeletedAt"], read["keepDays"]), (300, 90))
        self.assertEqual(read["title"], "The Silver Lock")  # the recap itself stays
        # A recap with no session at its time (none was ever saved) is not marked.
        await self.store.enqueue(THURSDAY.id, self.one.id, 500, dm_user_id=ALICE.id)
        loose = await read_recap(
            self.web, self.session, notify.job_ref(THURSDAY.id, self.one.id, 500)
        )
        self.assertIsNone(loose["transcriptsDeletedAt"])

    async def test_a_recap_says_when_a_fixed_spelling_changed_its_text(self) -> None:
        # Never respelled: null. The bot writes respelled_at when it carries a fixed spelling into
        # the text (#2170); the page reads it and tells the DM (#2178).
        self.assertIsNone((await read_recap(self.web, self.session, self.ref))["respelledAt"])
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET respelled_at = 777 WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = 100",
                (THURSDAY.id, self.one.id),
            )
        self.assertEqual((await read_recap(self.web, self.session, self.ref))["respelledAt"], 777)
        listed = await list_recaps(self.web, self.session)
        self.assertEqual({r["campaign"]: r["respelledAt"] for r in listed}["One"], 777)

    async def test_the_month_line_counts_only_the_dms_own_campaigns(self) -> None:
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready")
        got = await month_usage(self.web, self.session, now=NOW, recaps_allowed=10, audio_allowed=8)
        self.assertEqual(
            got,
            [
                {
                    "serverName": THURSDAY.name,
                    "campaign": "One",
                    "used": 1,
                    "allowed": 10,
                    "audioUsed": 1,
                    "audioAllowed": 8,
                }
            ],
        )  # Bob's campaign "Two" is not Alice's to see
        later = await month_usage(
            self.web, self.session, now=NOW + 90 * 86400, recaps_allowed=10, audio_allowed=8
        )
        self.assertEqual(later, [])  # a new month starts from nothing

    async def test_another_campaigns_recap_looks_like_none_at_all(self) -> None:
        for call in (
            read_recap(self.web, self.session, self.other),
            decide(self.web, self.session, self.other, "post"),
            edit_recap(self.web, self.session, self.other, summary="x", housekeeping=[]),
        ):
            with self.assertRaises(RecapError) as caught:
                await call
            self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual((await self.state(self.two.id)).status, "draft")

    async def test_a_server_outside_the_sessions_list_and_a_bad_ref_are_not_found(self) -> None:
        for ref in (notify.job_ref(999, self.one.id, 100), "nonsense", f"111.{self.one.id}.7"):
            with self.assertRaises(RecapError):
                await read_recap(self.web, self.session, ref)

    async def test_an_edit_is_saved_and_the_original_is_kept(self) -> None:
        shown = await edit_recap(
            self.web, self.session, self.ref, summary="《Calm》 Edited by the DM.",
            housekeeping=["  Mira   rested. ", ""],
        )  # fmt: skip
        self.assertEqual(shown["housekeeping"], ["Mira rested."])
        job = await self.state(self.one.id)
        self.assertEqual(job.summary, "《Calm》 Edited by the DM.")
        self.assertEqual(job.original, fx.SUMMARY)  # the diff against the draft stays possible

    async def test_edits_that_are_empty_or_too_big_are_refused(self) -> None:
        for summary, bullets in (
            ("  ", []),
            ("x" * (SUMMARY_MAX + 1), []),
            ("ok", ["b"] * 13),
            ("ok", ["b" * 141]),
        ):
            with self.assertRaises(RecapError) as caught:
                await edit_recap(
                    self.web, self.session, self.ref, summary=summary, housekeeping=bullets
                )
            self.assertEqual(caught.exception.code, "bad_text")

    async def test_the_website_cannot_touch_the_workers_lease(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column in (
                "leased_to", "decided_at", "updated_at", "attempt", "errors", "next_at",
                "beats", "problems", "last_error", "restarts",
            ):  # fmt: skip
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE recap_jobs SET {column} = {column}")

    async def test_the_website_cannot_write_the_bots_columns(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column, value in (("speakers", "'{5}'"), ("calls", "0"), ("original", "'x'"),
                                  ("dm_user_id", "5"), ("emailed_at", "5")):  # fmt: skip
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE recap_jobs SET {column} = {value}")

    async def test_a_player_who_is_not_a_dm_cannot_even_read_it(self) -> None:
        await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE user_id = %s", (ALICE.id,))
        session = await sessions.find(self.web, token, now=NOW)
        assert session is not None
        self.assertEqual(await list_recaps(self.web, session), [])

    async def test_post_marks_it_for_the_bot_and_only_once(self) -> None:
        shown = await decide(self.web, self.session, self.ref, "post")
        self.assertEqual(shown["status"], "posting")
        self.assertFalse(shown["canEdit"] or shown["canPost"])
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")
        with self.assertRaises(RecapError):  # no edits once it is on its way
            await edit_recap(self.web, self.session, self.ref, summary="x", housekeeping=[])

    async def test_dont_post_leaves_it_out_for_good(self) -> None:
        shown = await decide(self.web, self.session, self.ref, "drop")
        self.assertEqual(shown["status"], "dropped")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "again")

    async def test_try_again_is_offered_twice_and_queues_a_new_job(self) -> None:
        first = await decide(self.web, self.session, self.ref, "again")
        self.assertEqual(
            (first["state"], first["triesLeft"], first["working"]), ("queued", 1, True)
        )
        job = await self.state(self.one.id)
        self.assertEqual((job.tries, job.state, job.beats), (1, "queued", []))
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'failed'")
        second = await decide(self.web, self.session, self.ref, "again")
        self.assertEqual(second["triesLeft"], 0)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'ready'")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "again")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "nonsense")

    async def test_a_recap_still_being_written_cannot_be_posted_or_edited(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'prose'")
        shown = await read_recap(self.web, self.session, self.ref)
        self.assertTrue(shown["working"])
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")


class TheRoutes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(recaps_on=True, recap_audio_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def mail(self) -> dict[str, Any]:
        got = await self.client.get(f"/me/recaps/{self.ref}")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertNotIn(BOB.email, got.text)  # never another DM's address (#2334)
        return dict(got.json()["recapEmail"])

    async def test_the_recap_says_which_address_its_email_goes_to_and_where_it_came_from(
        self,
    ) -> None:
        mail = await self.mail()
        self.assertEqual(
            (mail["source"], mail["address"], mail["on"]), ("signin", ALICE.email, True)
        )
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'alice-pay@example.org', payment_email_at = 1"
                " WHERE user_id = %s",
                (ALICE.id,),
            )
        mail = await self.mail()
        self.assertEqual((mail["source"], mail["address"]), ("payment", "alice-pay@example.org"))
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = NULL, email = '' WHERE user_id = %s",
                (ALICE.id,),
            )
        mail = await self.mail()
        self.assertEqual((mail["source"], mail["address"]), ("none", None))

    async def test_the_recap_says_when_the_campaigns_email_switch_is_off(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET recaps_email = false WHERE id = %s", (self.one.id,)
            )
        self.assertFalse((await self.mail())["on"])

    async def test_list_read_edit_and_decide(self) -> None:
        listed = await self.client.get("/me/recaps")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()["recaps"]), 1)
        (month,) = listed.json()["recapsThisMonth"]
        self.assertEqual((month["campaign"], month["used"], month["allowed"]), ("One", 1, 10))
        got = await self.client.get(f"/me/recaps/{self.ref}")
        self.assertEqual(got.json()["title"], "The Silver Lock")
        saved = await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": "《Calm》 Hi.", "housekeeping": []}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        posted = await self.client.post(f"/me/recaps/{self.ref}/post")
        self.assertEqual(posted.json()["status"], "posting")
        again = await self.client.post(f"/me/recaps/{self.ref}/post")
        self.assertEqual((again.status_code, again.json()["error"]), (409, "not_allowed"))

    async def test_not_found_bad_body_and_signed_out(self) -> None:
        self.assertEqual((await self.client.get(f"/me/recaps/{self.other}")).status_code, 404)
        self.assertEqual((await self.client.get("/me/recaps/nonsense")).status_code, 404)
        bad = await self.client.put(f"/me/recaps/{self.ref}", json={"summary": 5})
        self.assertEqual(bad.status_code, 422)
        big = await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": "x" * 8000, "housekeeping": []}
        )
        self.assertEqual((big.status_code, big.json()["error"]), (422, "bad_text"))
        self.assertEqual((await self.client.post(f"/me/recaps/{self.ref}/zap")).status_code, 404)
        anonymous = httpx.AsyncClient(
            transport=self.client._transport,
            base_url=API,
            headers=HEADERS,
        )
        self.assertEqual((await anonymous.get("/me/recaps")).status_code, 401)
        await anonymous.aclose()


class TheAudio(Base):
    """The recap audio on the website (#1418): who is handed a link, what a link opens, and the
    one door that asks for the audio again."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config = settings(recap_audio_dir=self.dir.name, recaps_on=True)
        app = create_app(
            self.config, self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.files = AudioFiles(Path(self.dir.name))

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def make(self, campaign: str, state: str = "ready", current: bool = True) -> None:
        job = await self.state(campaign)
        digest = text_hash(job.title, job.summary) if current else "0" * 64
        await self.store.set_audio(
            THURSDAY.id, campaign, 100, audio=state, text_hash=digest, error=""
        )
        for kind in KINDS:
            path = self.files.path(THURSDAY.id, campaign, 100, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"audio-{kind}".encode())

    async def test_the_recap_says_where_its_audio_is(self) -> None:
        await self.approved_with_audio()
        off = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual(
            off,
            {"state": "off", "error": "", "current": False, "canAskAgain": False,
             "postedNote": False},
        )  # fmt: skip
        await self.make(self.one.id)
        ready = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual(
            (ready["state"], ready["current"], ready["canAskAgain"]), ("ready", True, True)
        )
        # After an edit the audio no longer matches the text: no players.
        await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": "《Calm》 Changed.", "housekeeping": []}
        )
        stale = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual((stale["state"], stale["current"]), ("ready", False))

    async def test_a_failed_audio_carries_its_sentence_and_may_be_asked_for_again(self) -> None:
        await self.approved_with_audio()
        await self.store.set_audio(
            THURSDAY.id, self.one.id, 100, audio="failed", error="The voice company is busy."
        )
        audio = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual((audio["state"], audio["error"]), ("failed", "The voice company is busy."))
        self.assertTrue(audio["canAskAgain"])

    async def test_the_dm_gets_three_links_and_each_opens_its_own_file(self) -> None:
        await self.make(self.one.id)
        got = await self.client.get(f"/me/recaps/{self.ref}/audio")
        self.assertEqual(got.status_code, 200, got.text)
        links = got.json()["links"]
        self.assertEqual(set(links), set(KINDS))
        for kind, url in links.items():
            fetched = await self.client.get(url.removeprefix(API))
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.content, f"audio-{kind}".encode())
            self.assertEqual(fetched.headers["content-type"], "audio/mpeg")
            self.assertIn("no-store", fetched.headers["cache-control"])

    async def test_no_links_when_the_audio_is_not_current_or_not_ready(self) -> None:
        await self.make(self.one.id, current=False)
        self.assertEqual(
            (await self.client.get(f"/me/recaps/{self.ref}/audio")).json(), {"links": {}}
        )
        await self.make(self.one.id, state="queued")
        self.assertEqual(
            (await self.client.get(f"/me/recaps/{self.ref}/audio")).json(), {"links": {}}
        )

    async def test_someone_who_is_not_this_campaigns_dm_gets_nothing(self) -> None:
        await self.make(self.two.id)
        got = await self.client.get(f"/me/recaps/{self.other}/audio")  # Bob's campaign
        self.assertEqual((got.status_code, got.json()["error"]), (404, "not_found"))
        anonymous = httpx.AsyncClient(
            transport=self.client._transport, base_url=API, headers=HEADERS
        )
        self.assertEqual((await anonymous.get(f"/me/recaps/{self.ref}/audio")).status_code, 401)
        await anonymous.aclose()

    async def test_a_link_opens_only_what_it_was_made_for(self) -> None:
        await self.make(self.one.id)
        await self.make(self.two.id)
        links = (await self.client.get(f"/me/recaps/{self.ref}/audio")).json()["links"]
        url = links["voice"].removeprefix(API)
        path, query = url.split("?")
        # Another recap, another kind, a changed signature or expiry, none or too long: nothing.
        for bad in (
            path.replace(self.ref, self.other) + "?" + query,
            path.replace("/voice", "/music") + "?" + query,
            path + "?" + query.replace("s=", "s=0"),
            path + "?e=1&s=" + query.split("s=")[1],
            path + "?e=" + "9" * 20 + "&s=x",
            path + "?" + query.split("&")[0],
            path,
            path.replace("/voice", "/..%2F..%2Fetc") + "?" + query,
            path.replace("/voice", "/passwd") + "?" + query,
        ):
            with self.subTest(bad):
                self.assertEqual((await self.client.get(bad)).status_code, 404)
        self.assertEqual((await self.client.get(url)).status_code, 200)

    async def test_odd_characters_in_a_link_are_a_not_found_not_a_crash(self) -> None:
        await self.make(self.one.id)
        url = (await self.client.get(f"/me/recaps/{self.ref}/audio")).json()["links"]["voice"]
        path, query = url.removeprefix(API).split("?")
        good_s = query.split("s=")[1]
        for bad in (
            f"{path}?e=1&s=%C3%A9",  # a non-ASCII signature
            f"{path}?e=%C2%B2&s={good_s}",  # "²": isdigit, but not a number
            f"{path.replace(self.ref, '%C2%B2.' + self.one.id + '.100')}?{query}",
            f"{path}?e=1&s={good_s.upper()}",
            f"{path}?e=1&s=" + "a" * 5000,
        ):
            with self.subTest(bad):
                self.assertEqual((await self.client.get(bad)).status_code, 404)
        self.assertEqual(
            (await self.client.get(f"/me/recaps/%C2%B2.{self.one.id}.1")).status_code, 404
        )
        self.assertEqual(
            (await self.client.get(f"/me/recaps/%C2%B2.{self.one.id}.1/audio")).status_code, 404
        )

    async def test_a_player_can_ask_for_part_of_the_file(self) -> None:
        await self.make(self.one.id)
        url = (await self.client.get(f"/me/recaps/{self.ref}/audio")).json()["links"]["voice"]
        part = await self.client.get(
            url.removeprefix(API), headers={**HEADERS, "Range": "bytes=0-4"}
        )
        self.assertEqual((part.status_code, part.content), (206, b"audio"))

    async def test_an_old_link_and_a_missing_file_open_nothing(self) -> None:
        await self.make(self.one.id)
        old = AudioFiles(Path(self.dir.name), clock=lambda: 1.0)
        expires, signature = old.sign(
            recap_audio.link_key(self.config.secret_key), THURSDAY.id, self.one.id, 100, "voice"
        )
        gone = await self.client.get(f"/recap-audio/{self.ref}/voice?e={expires}&s={signature}")
        self.assertEqual(gone.status_code, 404)
        links = (await self.client.get(f"/me/recaps/{self.ref}/audio")).json()["links"]
        self.files.path(THURSDAY.id, self.one.id, 100, "voice").unlink()
        self.assertEqual((await self.client.get(links["voice"].removeprefix(API))).status_code, 404)

    async def test_without_a_folder_there_are_no_links_and_no_files(self) -> None:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        await self.make(self.one.id)
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.assertEqual((await client.get(f"/me/recaps/{self.ref}/audio")).json(), {"links": {}})
        await client.aclose()

    async def approved_with_audio(self) -> None:
        """The DM approved this very text and chose both tracks (#2083): "again" needs that."""
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET audio_choice = 'both', approved_hash = encode(sha256("
                "convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
            )

    async def posted_with_old_audio(self, *, current: bool = False) -> None:
        """A posted recap, approved with both tracks, whose audio is (or is not) from this text."""
        await self.approved_with_audio()
        await self.make(self.one.id, current=current)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")

    async def test_a_posted_recap_with_stale_audio_can_be_narrated_again(self) -> None:
        await self.posted_with_old_audio()
        page = (await self.client.get(f"/me/recaps/{self.ref}")).json()
        self.assertEqual(page["status"], "posted")
        self.assertTrue(page["audio"]["canAskAgain"])
        self.assertFalse(page["audio"]["current"])
        done = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["audio"]["state"], "queued")
        job = await self.state(self.one.id)
        self.assertEqual((job.status, job.audio, job.audio_tries), ("posted", "queued", 0))
        # The audio step may take it now, and its files are for the page and the shared link.
        self.assertIn((self.one.id, 100), await self.store.audio_due(THURSDAY.id))
        self.assertTrue(await self.store.audio_lease(THURSDAY.id, self.one.id, 100))
        self.assertTrue(
            await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready", text_hash="a")
        )

    async def test_a_posted_recap_whose_audio_is_current_is_refused(self) -> None:
        await self.posted_with_old_audio(current=True)
        page = (await self.client.get(f"/me/recaps/{self.ref}")).json()
        self.assertFalse(page["audio"]["canAskAgain"])
        refused = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((refused.status_code, refused.json()["error"]), (409, "not_allowed"))
        self.assertEqual((await self.state(self.one.id)).audio, "ready")

    async def test_a_posted_recap_with_no_audio_or_audio_under_way_is_not_offered_again(
        self,
    ) -> None:
        await self.posted_with_old_audio()
        for state in ("off", "queued"):
            with self.subTest(state):
                await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio=state)
                refused = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
                self.assertEqual(refused.status_code, 409)

    async def test_a_failed_remake_of_a_posted_recap_can_be_tried_again_after_ten_minutes(
        self,
    ) -> None:
        await self.posted_with_old_audio()
        first = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(first.status_code, 200, first.text)
        await self.store.set_audio(
            THURSDAY.id, self.one.id, 100, audio="failed", error="busy", text_hash=""
        )
        page = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual(
            (page["state"], page["canAskAgain"], page["postedNote"]), ("failed", True, True)
        )
        soon = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((soon.status_code, soon.json()["error"]), (429, "too_soon"))
        async with self.db.guild(THURSDAY.id) as conn:  # ten minutes pass
            await conn.execute("UPDATE recap_jobs SET audio_asked_at = audio_asked_at - 601")
        again = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((await self.state(self.one.id)).audio, "queued")

    async def test_a_remake_starts_its_own_credit_count_but_a_retry_keeps_it(self) -> None:
        await self.posted_with_old_audio()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET audio_credits = 8000")
        done = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((await self.state(self.one.id)).audio_credits, 0)  # a new making
        await self.store.set_audio(
            THURSDAY.id, self.one.id, 100, audio="failed", credits=3000, text_hash=""
        )
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET audio_asked_at = 0")
        await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((await self.state(self.one.id)).audio_credits, 3000)  # the same making

    async def test_a_posted_recap_page_says_the_discord_message_keeps_its_old_audio(self) -> None:
        await self.posted_with_old_audio()
        audio = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertTrue(audio["postedNote"])

    async def test_a_draft_recap_is_held_to_the_plan_too(self) -> None:
        await self.approved_with_audio()
        await self.make(self.one.id)
        with self.assertRaises(RecapError) as plan:
            await audio_again(self.web, self.session, self.ref, audio_in_plan=False)
        self.assertEqual(plan.exception.code, "not_in_plan")
        self.assertEqual((await self.state(self.one.id)).audio, "ready")

    async def test_nothing_is_queued_while_recaps_are_switched_off(self) -> None:
        await self.posted_with_old_audio()
        app = create_app(
            settings(recap_audio_dir=self.dir.name), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        got = await client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "audio_off"))
        self.assertEqual((await self.state(self.one.id)).audio, "ready")

    async def test_the_door_itself_refuses_a_member_who_is_not_a_dm_of_the_campaign(self) -> None:
        """Alice is Bob's neighbour in the server but not a DM of "Two": the SECURITY DEFINER
        door must say no itself, not lean on the routes' checks."""
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posted', audio = 'ready', audio_choice = 'both',"
                " audio_text_hash = %s, approved_hash = encode(sha256(convert_to(title || E'\\n'"
                " || summary, 'UTF8')), 'hex') WHERE campaign_id = %s",
                ("0" * 64, self.two.id),
            )
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_recap_audio_again(%s, %s, 100) AS r", (THURSDAY.id, self.two.id)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"], "not_allowed")
        self.assertEqual((await self.state(self.two.id)).audio, "ready")

    async def test_a_posted_recap_needs_the_plan_the_approval_and_the_ten_minutes(self) -> None:
        await self.posted_with_old_audio()
        with self.assertRaises(RecapError) as plan:  # the owner's plan has no narration
            await audio_again(self.web, self.session, self.ref, audio_in_plan=False)
        self.assertEqual(plan.exception.code, "not_in_plan")
        self.assertEqual((await self.state(self.one.id)).audio, "ready")  # nothing queued
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET summary = summary || ' More.'")
        changed = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((changed.status_code, changed.json()["error"]), (409, "needs_approval"))
        await self.approved_with_audio()  # approved again
        first = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(first.status_code, 200, first.text)
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready", text_hash="0" * 64)
        soon = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((soon.status_code, soon.json()["error"]), (429, "too_soon"))

    async def test_the_route_refuses_a_posted_recap_when_the_plan_has_no_narration(self) -> None:
        await self.posted_with_old_audio()
        app = create_app(
            settings(enforce_plans=True, recaps_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        got = await client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "not_in_plan"))
        self.assertEqual((await self.state(self.one.id)).audio, "ready")  # nothing was queued

    async def test_only_the_campaigns_dm_may_narrate_a_posted_recap_again(self) -> None:
        await self.posted_with_old_audio()
        got = await self.client.post(f"/me/recaps/{self.other}/audio/again")  # Bob's campaign
        self.assertEqual(got.status_code, 404)

    async def test_narrating_again_does_not_count_a_second_narrated_recap(self) -> None:
        await self.posted_with_old_audio()
        before = await self.store.used_in_month(THURSDAY.id, self.one.id, NOW)
        await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready", text_hash="0" * 64)
        after = await self.store.used_in_month(THURSDAY.id, self.one.id, NOW)
        self.assertEqual(before.narrated, after.narrated)  # the meter charged once

    async def test_asking_again_after_the_text_changed_needs_a_new_approval(self) -> None:
        await self.approved_with_audio()
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET summary = summary || ' More.'")
        got = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "needs_approval"))

    async def test_asking_again_queues_it_and_waits_ten_minutes(self) -> None:
        await self.approved_with_audio()
        await self.store.set_audio(
            THURSDAY.id, self.one.id, 100, audio="failed", tries=2, error="x"
        )
        done = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["audio"]["state"], "queued")
        job = await self.state(self.one.id)
        self.assertEqual(
            (job.audio, job.audio_tries, job.audio_next_at, job.audio_error), ("queued", 0, 0, "")
        )
        # While the bot is making it the answer is no; once it has failed again, "too soon".
        busy = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((busy.status_code, busy.json()["error"]), (409, "not_allowed"))
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="failed", error="x")
        soon = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((soon.status_code, soon.json()["error"]), (429, "too_soon"))
        # Ten minutes later it may be asked for again (the database's own clock, so move the mark).
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET audio_asked_at = audio_asked_at - 601")
        later = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual(later.status_code, 200, later.text)

    async def test_the_door_asks_only_where_the_page_does(self) -> None:
        for state in ("queued", "off"):
            with self.subTest(state):
                await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio=state)
                refused = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
                self.assertEqual(
                    (refused.status_code, refused.json()["error"]), (409, "not_allowed")
                )
                self.assertEqual((await self.state(self.one.id)).audio, state)

    async def test_the_door_refuses_a_stranger_a_decided_recap_and_a_recap_in_progress(
        self,
    ) -> None:
        self.assertEqual(
            (await self.client.post(f"/me/recaps/{self.other}/audio/again")).status_code, 404
        )
        await decide(self.web, self.session, self.ref, "drop")
        dropped = await self.client.post(f"/me/recaps/{self.ref}/audio/again")
        self.assertEqual((dropped.status_code, dropped.json()["error"]), (409, "not_allowed"))
        await self.store.update(THURSDAY.id, self.two.id, 100, state="beats")
        with self.assertRaises(RecapError):  # Alice is not Two's DM
            await audio_again(self.web, self.session, self.other)

    async def test_the_website_cannot_write_the_audio_columns_itself(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column in (
                "audio",
                "audio_tries",
                "audio_next_at",
                "audio_asked_at",
                "audio_text_hash",
            ):
                with (
                    self.subTest(column),
                    self.assertRaises(psycopg.errors.InsufficientPrivilege),
                ):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE recap_jobs SET {column} = {column}")

    async def test_the_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_recap_audio_again"), {"schema.py", "web/recaps.py"})


class TheApproval(Base):
    """Cue problems on save, and approving the text with a choice of audio (#2083)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(recaps_on=True, recap_audio_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        async with self.db.guild(THURSDAY.id) as conn:  # the speaker is recorded (consent given)
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, %s, %s)",
                (THURSDAY.id, fx.ALICE, NOW, TERMS_VERSION),
            )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def save(self, summary: str) -> httpx.Response:
        return await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": summary, "housekeeping": []}
        )

    async def approve(self, choice: str, ref: str = "") -> httpx.Response:
        return await self.client.post(
            f"/me/recaps/{ref or self.ref}/approve", json={"choice": choice}
        )

    async def test_the_recap_carries_the_allowed_words_and_no_approval_yet(self) -> None:
        got = (await self.client.get(f"/me/recaps/{self.ref}")).json()
        words = got["cue_words"]
        self.assertEqual(len(words["moods"]), 18)
        self.assertIn("tension", words["synonyms"])
        self.assertIn("long pause", words["pacing"])
        self.assertEqual(
            got["approval"],
            {"approved": False, "choice": "", "approvedAt": None, "canApprove": True},
        )

    async def test_a_save_with_wrong_cues_is_refused_with_where_and_nothing_is_saved(self) -> None:
        bad = "《Tens》 Mira waited ((pausse)) for him.\n\n《calm》 Then it ended."
        got = await self.save(bad)
        self.assertEqual(got.status_code, 422)
        body = got.json()
        self.assertEqual(body["error"], "cue_problems")
        first, second = body["cue_problems"]
        self.assertEqual(
            (first["kind"], first["suggest"], first["paragraph"], first["cue"]),
            ("unknown", "tense", 1, "《Tens》"),
        )
        self.assertEqual((second["kind"], second["suggest"]), ("unknown", "pause"))
        self.assertIn("Did you mean tense?", first["words"])
        self.assertEqual((await self.state(self.one.id)).summary, fx.SUMMARY)  # unchanged

    async def test_a_save_with_good_cues_works(self) -> None:
        got = await self.save("《Calm》 Mira waited ((pause)) for him.")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(
            (await self.state(self.one.id)).summary, "《Calm》 Mira waited ((pause)) for him."
        )

    async def test_each_choice_approves_and_only_audio_choices_queue_audio(self) -> None:
        for choice, queued in (
            ("text", "off"),
            ("voice", "queued"),
            ("music", "queued"),
            ("both", "queued"),
        ):
            with self.subTest(choice):
                await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="off")
                got = await self.approve(choice)
                self.assertEqual(got.status_code, 200, got.text)
                self.assertEqual(got.json()["approval"]["choice"], choice)
                job = await self.state(self.one.id)
                self.assertEqual(
                    (job.audio, job.audio_choice, job.approved), (queued, choice, True)
                )
                self.assertEqual(job.approved_by, ALICE.id)

    async def test_an_approval_with_wrong_cues_approves_nothing(self) -> None:
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="《Tens》 Mira waited.")
        got = await self.approve("both")
        self.assertEqual((got.status_code, got.json()["error"]), (422, "cue_problems"))
        job = await self.state(self.one.id)
        self.assertEqual((job.approved, job.audio), (False, "off"))

    async def test_an_edit_ends_the_approval(self) -> None:
        self.assertEqual((await self.approve("text")).status_code, 200)
        self.assertTrue((await self.state(self.one.id)).approved)
        await self.save("《Calm》 The DM rewrote it.")
        got = (await self.client.get(f"/me/recaps/{self.ref}")).json()
        self.assertFalse(got["approval"]["approved"])
        self.assertEqual(got["approval"]["choice"], "")

    async def test_a_stranger_a_bad_choice_and_a_decided_recap_are_refused(self) -> None:
        self.assertEqual((await self.approve("both", self.other)).status_code, 404)  # Bob's recap
        self.assertEqual((await self.approve("loud")).status_code, 422)
        self.assertEqual(
            (await self.client.post(f"/me/recaps/{self.ref}/approve", json={})).status_code, 422
        )
        await decide(self.web, self.session, self.ref, "drop")
        got = await self.approve("both")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "not_allowed"))

    async def test_approve_and_post_hands_it_to_the_bot_in_one_call(self) -> None:
        """(#2419) ✅ Approve and post text only: approved with the text-only choice and posting."""
        got = await self.client.post(
            f"/me/recaps/{self.ref}/approve", json={"choice": "text", "post": True}
        )
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["status"], body["approval"]["choice"]), ("posting", "text"))
        job = await self.state(self.one.id)
        self.assertEqual((job.status, job.approved), ("posting", True))

    async def test_approve_and_post_is_only_for_the_text_only_choice(self) -> None:
        got = await self.client.post(
            f"/me/recaps/{self.ref}/approve", json={"choice": "both", "post": True}
        )
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_choice"))
        got = await self.client.post(
            f"/me/recaps/{self.ref}/approve", json={"choice": "text", "post": "yes"}
        )
        self.assertEqual(got.status_code, 422)
        job = await self.state(self.one.id)
        self.assertEqual((job.status, job.approved), ("draft", False))  # nothing half done

    async def test_approve_and_post_that_cannot_post_approves_nothing(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:  # decided in Discord meanwhile
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")
        got = await self.client.post(
            f"/me/recaps/{self.ref}/approve", json={"choice": "text", "post": True}
        )
        self.assertEqual((got.status_code, got.json()["error"]), (409, "not_allowed"))
        job = await self.state(self.one.id)
        self.assertEqual((job.status, job.approved), ("posted", False))  # no approval was recorded

    async def test_audio_is_refused_by_the_route_where_recaps_are_off(self) -> None:
        """With recaps off the route refuses audio (409) and still approves text."""
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        ) as off:
            off.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            url = f"/me/recaps/{self.ref}/approve"
            got = await off.post(url, json={"choice": "both"})
            self.assertEqual((got.status_code, got.json()["error"]), (409, "audio_unavailable"))
            self.assertEqual((await off.post(url, json={"choice": "text"})).status_code, 200)

    async def test_approving_without_posting_leaves_a_draft_that_can_be_posted_later(self) -> None:
        """(#2419) 📋 Approve but don't post: approved, nothing sent; Post to the table later."""
        got = await self.approve("text")
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["status"], body["approval"]["approved"]), ("draft", True))
        self.assertTrue(body["canPost"])
        later = await decide(self.web, self.session, self.ref, "post")
        self.assertEqual(later["status"], "posting")

    async def test_audio_is_refused_where_nothing_here_makes_it(self) -> None:
        from dmbot.web.recaps import approve_recap

        for why in ("not_set_up", "allowance"):
            with self.subTest(why), self.assertRaises(RecapError) as no:
                await approve_recap(
                    self.web, self.session, self.ref, "both",
                    audio_in_plan=True, audio_unavailable=why,
                )  # fmt: skip
            self.assertEqual(no.exception.code, "audio_unavailable")
        got = await approve_recap(  # text only is never held back by it
            self.web, self.session, self.ref, "text",
            audio_in_plan=True, audio_unavailable="not_set_up",
        )  # fmt: skip
        self.assertEqual(got["approval"]["choice"], "text")

    async def test_audio_already_under_way_is_not_started_over(self) -> None:
        self.assertEqual((await self.approve("both")).status_code, 200)
        again = await self.approve("voice")
        self.assertEqual(again.status_code, 409)
        self.assertEqual((await self.state(self.one.id)).audio_choice, "both")

    async def test_a_plan_without_audio_may_only_approve_text(self) -> None:
        from dmbot.web.recaps import approve_recap

        with self.assertRaises(RecapError) as no:
            await approve_recap(self.web, self.session, self.ref, "both", audio_in_plan=False)
        self.assertEqual(no.exception.code, "not_in_plan")
        got = await approve_recap(self.web, self.session, self.ref, "text", audio_in_plan=False)
        self.assertEqual(got["approval"]["choice"], "text")

    async def test_the_website_cannot_write_the_approval_columns_itself(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column in ("approved_by", "approved_at", "approved_hash", "audio_choice"):
                with (
                    self.subTest(column),
                    self.assertRaises(psycopg.errors.InsufficientPrivilege),
                ):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE recap_jobs SET {column} = {column}")

    async def door(self, expected: str, user: Any = None, choice: str = "text") -> str:
        who = user or ALICE
        async with self.web.user(who.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_recap_approve(%s, %s, %s, %s, %s) AS r",
                (THURSDAY.id, self.one.id, 100, choice, expected),
            )
            return str((await cur.fetchone() or {})["r"])

    async def test_the_door_approves_only_the_text_that_was_checked(self) -> None:
        job = await self.state(self.one.id)
        self.assertEqual(await self.door("0" * 64), "not_allowed")  # an edit slipped in between
        self.assertEqual(await self.door(text_hash(job.title, job.summary)), "approved")
        # The SQL fingerprint is the Python one, for a title with multi-byte characters too.
        await self.store.update(THURSDAY.id, self.one.id, 100, title="Le château d'Æthelred 《x》")
        job = await self.state(self.one.id)
        self.assertEqual(await self.door(text_hash(job.title, job.summary)), "approved")
        self.assertTrue((await self.state(self.one.id)).approved)

    async def test_the_door_refuses_a_non_dm_a_bad_choice_and_a_recap_not_ready(
        self,
    ) -> None:
        job = await self.state(self.one.id)
        good = text_hash(job.title, job.summary)
        self.assertEqual(await self.door(good, BOB), "not_allowed")
        self.assertEqual(await self.door(good, choice="loud"), "not_allowed")
        await self.store.update(THURSDAY.id, self.one.id, 100, state="beats")
        self.assertEqual(await self.door(good), "not_allowed")

    async def names(self, secret: str, allowed: str = "") -> None:
        from dmbot.memory.models import DM as DM_SOURCE
        from dmbot.memory.store import MemoryStore

        memory = MemoryStore(self.db)
        g, c = THURSDAY.id, self.one.id
        hall = await memory.add_entity(
            g, c, type="place", name=allowed or "Quiet Hall", source=DM_SOURCE, status="confirmed"
        )
        await memory.add_alias(
            g, c, hall.value.id, secret, kind="title", source=DM_SOURCE, status="confirmed",
            secret=True,
        )  # fmt: skip

    async def test_a_text_that_names_a_secret_is_not_approved_and_the_name_is_not_returned(
        self,
    ) -> None:
        await self.names("Mira")  # the canned text says Mira
        got = await self.approve("text")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "names_secret"))
        self.assertNotIn("Mira", got.text)
        self.assertFalse((await self.state(self.one.id)).approved)

    async def test_a_player_who_stopped_being_recorded_stops_the_approval(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM consent WHERE user_id = %s", (fx.ALICE,))
        await self.store.update(THURSDAY.id, self.one.id, 100, speakers=[fx.ALICE])
        got = await self.approve("text")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stopped_recording"))

    async def test_make_the_audio_again_needs_a_current_approval(self) -> None:
        from dmbot.web.recaps import audio_again

        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="ready", tries=0)
        with self.assertRaises(RecapError):  # never approved
            await audio_again(self.web, self.session, self.ref)
        self.assertEqual((await self.approve("text")).status_code, 200)
        with self.assertRaises(RecapError):  # approved, but text only
            await audio_again(self.web, self.session, self.ref)
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="off")
        self.assertEqual((await self.approve("voice")).status_code, 200)
        await self.store.set_audio(THURSDAY.id, self.one.id, 100, audio="failed")
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="《Calm》 It was rewritten.")
        with self.assertRaises(RecapError):  # the text changed since: the approval is stale
            await audio_again(self.web, self.session, self.ref)

    async def test_nothing_is_approved_over_audio_that_is_queued_text_only_included(self) -> None:
        self.assertEqual((await self.approve("both")).status_code, 200)  # queued
        for choice in ("text", "voice", "music", "both"):
            got = await self.approve(choice)
            self.assertEqual(got.status_code, 409, choice)
        self.assertEqual((await self.state(self.one.id)).audio_choice, "both")

    async def test_the_names_door_has_one_caller_and_answers_only_a_dm(self) -> None:
        self.assertEqual(callers("dmbot_recap_check_names"), {"schema.py", "web/recaps.py"})
        async with self.web.user(BOB.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_recap_check_names(%s, %s, %s, NULL) AS r",
                (THURSDAY.id, self.one.id, TERMS_VERSION),
            )
            self.assertIsNone((await cur.fetchone() or {})["r"])  # Bob is no DM of One

    async def test_the_names_door_counts_a_name_the_players_dont_know_yet_as_secret(self) -> None:
        """#2321: the DM's "Players don't know yet" entries are secret to the recap check."""
        from dmbot.memory.store import MemoryStore

        memory = MemoryStore(self.db)
        g, c = THURSDAY.id, self.one.id
        for name in ("Jack", "The Shadow"):
            await memory.add_entity(
                g, c, type="character", name=name, source="dm", status="confirmed",
                hidden_from_players=name == "The Shadow",
            )  # fmt: skip
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(g),))
            cur = await conn.execute(
                "SELECT dmbot_recap_check_names(%s, %s, %s, NULL) AS r", (g, c, TERMS_VERSION)
            )
            got = (await cur.fetchone() or {})["r"]
        assert got is not None
        self.assertIn("The Shadow", got["secret"])
        self.assertNotIn("The Shadow", got["allowed"])
        self.assertIn("Jack", got["allowed"])

    async def test_consent_given_under_old_terms_does_not_count(self) -> None:
        """The website reads consent the way the bot does (`consent._select`): the current terms
        version, and the outside engine in use when there is one."""
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE user_id = %s",
                (TERMS_VERSION - 1, fx.ALICE),
            )
        got = await self.approve("text")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stopped_recording"))
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = %s, outside_to = 'cloud' WHERE user_id = %s",
                (TERMS_VERSION, fx.ALICE),
            )
        self.assertEqual((await self.approve("text")).status_code, 200)  # no outside engine
        other = create_app(
            replace(settings(), outside_engine="deepgram"), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=other), base_url=API, headers=HEADERS
        )
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        got = await self.approve("text")  # said yes for another outside company
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stopped_recording"))

    async def test_the_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_recap_approve"), {"schema.py", "web/recaps.py"})


class TheAddressDoor(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)

    async def test_it_answers_for_a_dm_with_a_recap_through_the_door(self) -> None:
        self.assertEqual(await self.store.address(THURSDAY.id, ALICE.id), ALICE.email)

    async def test_it_answers_nothing_for_anyone_else_or_with_the_door_shut(self) -> None:
        self.assertIsNone(await self.store.address(THURSDAY.id, 55555))  # no recap job
        async with self.db.guild(THURSDAY.id) as conn:  # the door not opened
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (ALICE.id,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["a"])
        async with self.db.recap_mail(111) as conn:  # another server's recaps are not visible
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (BOB.id,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["a"])

    async def test_a_co_dm_with_no_job_of_their_own_gets_their_address(self) -> None:
        carol = DiscordUser(id=3003, name="Carol", email="carol@example.com")
        await sessions.sign_in(self.web, carol, [THURSDAY], now=NOW, days=30)
        self.assertIsNone(await self.store.address(THURSDAY.id, carol.id))  # not a DM yet
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, carol.id)
        self.assertEqual(await self.store.address(THURSDAY.id, carol.id), carol.email)

    async def test_a_co_dm_of_a_campaign_with_only_a_held_recap_gets_their_address(self) -> None:
        carol = DiscordUser(id=3003, name="Carol", email="carol@example.com")
        await sessions.sign_in(self.web, carol, [THURSDAY], now=NOW, days=30)
        held = await self.campaigns.create(THURSDAY.id, "Held", ALICE.id)
        await self.campaigns.add_dm(THURSDAY.id, held.id, carol.id)
        self.assertIsNone(await self.store.address(THURSDAY.id, carol.id))  # nothing waiting yet
        await HoldStore(self.db).hold(
            THURSDAY.id, held.id, 300, session_id="s1", dm_user_id=ALICE.id, channel_id=5,
            asked=True, names_open=2,
        )  # fmt: skip
        self.assertEqual(await self.store.address(THURSDAY.id, carol.id), carol.email)

    async def test_a_hold_gives_its_own_campaigns_dms_their_address_and_nobody_else(self) -> None:
        carol = DiscordUser(id=3003, name="Carol", email="carol@example.com")
        erin = DiscordUser(id=5005, name="Erin", email="erin@example.com")
        dave = DiscordUser(id=4004, name="Dave", email="dave@example.com")
        for who in (carol, erin, dave):
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30)
        held = await self.campaigns.create(THURSDAY.id, "Held", carol.id)  # no job of its own
        await self.campaigns.create(THURSDAY.id, "Quiet", erin.id)  # no job, no hold
        await HoldStore(self.db).hold(
            THURSDAY.id, held.id, 300, session_id="s1", dm_user_id=carol.id, channel_id=5,
            asked=True, names_open=2,
        )  # fmt: skip
        self.assertEqual(await self.store.address(THURSDAY.id, carol.id), carol.email)
        self.assertIsNone(await self.store.address(THURSDAY.id, erin.id))  # another campaign's DM
        self.assertIsNone(await self.store.address(THURSDAY.id, dave.id))  # not a DM at all

    async def test_a_former_dm_gets_nothing_even_if_the_job_was_made_for_them(self) -> None:
        self.assertEqual(await self.store.address(THURSDAY.id, ALICE.id), ALICE.email)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "DELETE FROM campaign_dms WHERE campaign_id = %s AND user_id = %s",
                (self.one.id, ALICE.id),
            )
        self.assertIsNone(await self.store.address(THURSDAY.id, ALICE.id))

    async def test_someone_who_is_not_a_dm_there_still_gets_none(self) -> None:
        dave = DiscordUser(id=4004, name="Dave", email="dave@example.com")
        await sessions.sign_in(self.web, dave, [THURSDAY], now=NOW, days=30)
        self.assertIsNone(await self.store.address(THURSDAY.id, dave.id))
        # a DM of a campaign that has no recap job does not count either
        empty = await self.campaigns.create(THURSDAY.id, "Empty", dave.id)
        self.assertIsNotNone(empty)
        self.assertIsNone(await self.store.address(THURSDAY.id, dave.id))

    def test_only_the_recap_code_opens_the_door(self) -> None:
        self.assertEqual(callers(".recap_mail("), {"recaps/store.py", "schema.py"})
        self.assertEqual(callers("dmbot_recap_address("), {"recaps/store.py", "schema.py"})
        self.assertEqual(callers("dmbot.recap_mail"), {"schema.py", "db.py"})


class Sent:
    def __init__(self) -> None:
        self.mail: list[Any] = []

    async def __call__(self, message: Any) -> None:
        self.mail.append(message)


class TheEmail(Base):
    async def test_the_dm_gets_the_title_and_a_link_never_the_recap(self) -> None:
        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        job = await self.state(self.one.id)
        result = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertEqual(result, "sent")
        body = outbox.mail[0].get_content()
        self.assertIn("The Silver Lock", body)
        self.assertIn(f"{SITE}/account/recaps/{self.ref}", body)
        self.assertNotIn("Dusk settled", body)
        self.assertNotIn("Oskar", body)
        # Once for each session and kind.
        again = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertEqual(again, "already_sent")

    async def test_a_failed_job_emails_the_reason_in_plain_words(self) -> None:
        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        job = replace(await self.state(self.one.id), state="failed", last_error="Try tomorrow.")
        await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertIn("No recap this time", outbox.mail[0]["Subject"])
        self.assertIn("Try tomorrow.", outbox.mail[0].get_content())

    async def test_no_site_address_or_no_account_address_sends_nothing(self) -> None:
        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        job = await self.state(self.one.id)
        self.assertEqual(
            await notify.tell_the_dm(mailer, self.store, "", THURSDAY.id, self.one.id, job),
            "not_ready",
        )
        # Bob has no website account, so there is no address to write to.
        bobs = await self.state(self.two.id)
        result = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.two.id, bobs)
        self.assertEqual(result, "no_address")
        self.assertEqual(outbox.mail, [])


def fake_bot(job: Job | None, *, allowed: bool = True, secrets: list[str] | None = None,
             channel: Any = "default", agreed: set[int] | None = None,
             present: dict[str, Any] | None = None,
             known: list[str] | None = None) -> Any:  # fmt: skip
    from dmbot.bot import DMBot

    sent = AsyncMock()
    sent.guild = SimpleNamespace(id=THURSDAY.id)
    store = SimpleNamespace(
        get=AsyncMock(return_value=job),
        posted=AsyncMock(return_value=True),
        back_to_draft=AsyncMock(),
        to_post=AsyncMock(return_value=[("c" * 32, 100)]),
    )
    campaign = SimpleNamespace(
        id="c" * 32, dmbchat_channel_id=55, dm_screen_channel_id=66,
        recaps_text=True, recaps_audio=True, recaps_email=True,
    )  # fmt: skip
    bot = SimpleNamespace(
        recaps=store,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        plan_allows=AsyncMock(return_value=allowed),
        post=AsyncMock(),
        get_channel=lambda cid: sent if channel == "default" else channel,
        recap_secret_names=AsyncMock(return_value=[] if secrets is None else secrets),
        recap_allowed_names=AsyncMock(return_value=known or []),
        consent=SimpleNamespace(
            consenting=AsyncMock(return_value=agreed if agreed is not None else {fx.ALICE})
        ),
    )
    bot.recap_audio_files = SimpleNamespace(present=lambda g, c, s: present or {})
    bot._post_one_recap = lambda g, c, a: DMBot._post_one_recap(cast(Any, bot), g, c, a)
    bot._recap_audio_files = lambda g, camp, j, ch: DMBot._recap_audio_files(
        cast(Any, bot), g, camp, j, ch
    )
    bot.sent = sent
    return bot


class ThePostStep(unittest.IsolatedAsyncioTestCase):
    def job(self, **kw: Any) -> Job:
        base = Job(
            100, fx.DM, "ready", 0, 0, 0, 0, 3, 0, 0, 3, "", (), [], "The Empty Hall",
            "《Tense》 ((slow)) Dusk fell on Mira.", "《Tense》 Dusk fell.",
            ("Mira drew her blade.",),
            ("m",), 4, "posting", 0, (fx.ALICE,), {}, None, 1, 1,
        )  # fmt: skip
        return replace(base, **kw)

    async def test_a_secret_inside_a_longer_confirmed_name_does_not_stop_the_post(self) -> None:
        """#2087: the owner's row. "Arcane Brotherhood" is secret; "Arcane Brotherhood Hall" is a
        confirmed name. The draft check read the confirmed names, so Post must too."""
        job = self.job(summary="《Tense》 Mira reached Arcane Brotherhood Hall at dusk.")
        bot = fake_bot(job, secrets=["Arcane Brotherhood"], known=["Arcane Brotherhood Hall"])
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.recaps.posted.assert_awaited_once()
        bot.recaps.back_to_draft.assert_not_awaited()

    async def test_the_secret_name_on_its_own_still_stops_the_post(self) -> None:
        job = self.job(summary="《Tense》 Mira feared the Arcane Brotherhood at dusk.")
        bot = fake_bot(job, secrets=["Arcane Brotherhood"], known=["Arcane Brotherhood Hall"])
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.recaps.posted.assert_not_awaited()
        bot.recaps.back_to_draft.assert_awaited_once()

    async def test_it_posts_without_cues_and_marks_it_posted_once(self) -> None:
        bot = fake_bot(self.job())
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        text = "".join(call.args[0] for call in bot.sent.send.await_args_list)
        self.assertIn("Dusk fell on Mira.", text)
        self.assertNotIn("《", text)
        self.assertIn("posted by your DM", text)
        bot.recaps.posted.assert_awaited_once()  # marked after the send, not before
        kwargs = bot.sent.send.await_args.kwargs
        self.assertEqual(kwargs["allowed_mentions"].users, False)

    def ready(self, **kw: Any) -> Job:
        from dmbot.recaps.approval import text_hash

        base = self.job()
        return self.job(audio="ready", audio_text_hash=text_hash(base.title, base.summary), **kw)

    async def with_files(self, job: Job, **kw: Any) -> Any:
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        voice, music_, mix_ = (Path(tmp.name) / n for n in ("v.mp3", "m.mp3", "x.mp3"))
        for f in (voice, music_, mix_):
            f.write_bytes(b"mp3")
        bot = fake_bot(job, present={"voice": voice, "music": music_, "mix": mix_}, **kw)
        return bot

    async def test_the_narration_and_music_go_as_attachments_on_the_last_message(self) -> None:
        bot = await self.with_files(self.ready())
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        calls = bot.sent.send.await_args_list
        for call in calls[:-1]:
            self.assertNotIn("files", call.kwargs)
        sent = calls[-1].kwargs["files"]
        self.assertEqual([f.filename for f in sent], ["recap-voice.mp3", "recap-music.mp3"])
        for f in sent:
            f.close()

    async def test_no_attachments_without_audio_or_when_the_plan_no_longer_has_it(self) -> None:
        for name, job, lapsed in (
            ("no audio", self.job(audio="off"), False),
            ("failed", self.job(audio="failed"), False),
            ("lapsed", self.ready(), True),
        ):
            with self.subTest(name):
                bot = await self.with_files(job)
                if lapsed:
                    bot.plan_allows = AsyncMock(side_effect=lambda a, *_, **__: a != "recap_audio")
                await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
                self.assertNotIn("files", bot.sent.send.await_args.kwargs)
                bot.recaps.posted.assert_awaited_once()  # the text is posted either way

    async def test_audio_made_from_older_text_is_left_out_and_the_dm_is_told(self) -> None:
        job = replace(self.ready(), summary="《Tense》 The DM changed this after the audio.")
        bot = await self.with_files(job)
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        self.assertNotIn("files", bot.sent.send.await_args.kwargs)
        told = bot.post.await_args.args[1]
        self.assertIn("text changed", told)
        bot.recaps.posted.assert_awaited_once()

    async def test_audio_too_big_for_the_servers_limit_is_left_out_and_the_dm_is_told(self) -> None:
        bot = await self.with_files(self.ready())
        bot.sent.guild.filesize_limit = 5  # bytes: three-byte files, two of them, are over it
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        self.assertNotIn("files", bot.sent.send.await_args.kwargs)
        self.assertIn("too big", bot.post.await_args.args[1])
        bot.recaps.posted.assert_awaited_once()

    async def test_a_file_that_has_gone_does_not_stop_the_post(self) -> None:
        bot = await self.with_files(self.ready())
        for path in bot.recap_audio_files.present(0, "", 0).values():
            path.unlink()
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        self.assertNotIn("files", bot.sent.send.await_args.kwargs)
        bot.recaps.posted.assert_awaited_once()

    async def test_each_check_that_fails_posts_nothing_and_tells_the_dm_why(self) -> None:
        cases = {
            "plan": fake_bot(self.job(), allowed=False),
            "channel": fake_bot(self.job(), channel=None),
            "other server": fake_bot(
                self.job(), channel=SimpleNamespace(send=AsyncMock(), guild=SimpleNamespace(id=2))
            ),
            "secret": fake_bot(self.job(), secrets=["Mira"]),
            "unreadable": fake_bot(self.job()),
            "consent": fake_bot(self.job(), agreed=set()),
        }
        cases["unreadable"].recap_secret_names = AsyncMock(return_value=None)
        for name, bot in cases.items():
            with self.subTest(name):
                await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
                bot.recaps.posted.assert_not_awaited()
                bot.recaps.back_to_draft.assert_awaited_once()
                note = bot.recaps.back_to_draft.await_args.kwargs["note"]
                self.assertTrue(note and note[0].isupper(), note)
                if hasattr(bot.sent, "send"):
                    bot.sent.send.assert_not_awaited()

    async def test_a_send_that_fails_puts_it_back_as_a_draft(self) -> None:
        bot = fake_bot(self.job())
        bot.sent.send.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.recaps.back_to_draft.assert_awaited_once()
        bot.recaps.posted.assert_not_awaited()

    async def test_a_job_that_is_not_waiting_to_post_is_left_alone(self) -> None:
        bot = fake_bot(self.job(status="draft"))
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.sent.send.assert_not_awaited()
        bot.recaps.posted.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
