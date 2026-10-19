"""The DM's recap page (#1382), run as the website's own database role: only a DM of the
campaign sees or changes a recap; edits, Post, Try again and Don't post are checked in the
database, not just the route; the address door answers only while it is open; and the bot's
Post step re-checks the plan, the chat channel, secret names and who is still recorded."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import psycopg

from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.recaps import notify
from dmbot.recaps.audio.files import KINDS, AudioFiles
from dmbot.recaps.audio.worker import text_hash
from dmbot.recaps.draft import SUMMARY_MAX
from dmbot.recaps.store import Job, RecapStore
from dmbot.transcript.store import TranscriptStore
from dmbot.web import recap_audio, sessions
from dmbot.web.app import create_app
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
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

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
        self.config = settings(recap_audio_dir=self.dir.name)
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
        off = (await self.client.get(f"/me/recaps/{self.ref}")).json()["audio"]
        self.assertEqual(off, {"state": "off", "error": "", "current": False, "canAskAgain": False})
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

    async def test_asking_again_queues_it_and_waits_ten_minutes(self) -> None:
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
        from dataclasses import replace

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
             present: dict[str, Any] | None = None) -> Any:  # fmt: skip
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
        from dataclasses import replace

        base = Job(
            100, fx.DM, "ready", 0, 0, 0, 0, 3, 0, 0, 3, "", (), [], "The Empty Hall",
            "《Tense》 ((slow)) Dusk fell on Mira.", "《Tense》 Dusk fell.",
            ("Mira drew her blade.",),
            ("m",), 4, "posting", 0, (fx.ALICE,), {}, None, 1, 1,
        )  # fmt: skip
        return replace(base, **kw)

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
        from dmbot.recaps.audio.worker import text_hash

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
        from dataclasses import replace

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
