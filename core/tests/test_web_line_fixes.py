"""A line fixed from the website's Transcripts page (#2858), run as the website's own database
role: who may fix what, the queue the bot reads, and what is never touched. Lines here are made
up."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.consent import TERMS_VERSION
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import line_fixes, sessions, transcripts
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings
from tests.test_web_transcripts import CAROL

START = 1_700_000_000
BASE_MS = (START + 86_400) * 1000
HEARD_AT = BASE_MS + 20_000  # Bob's line
ALICE_AT = BASE_MS + 1_000  # Alice's line (she DMs "One")


def lines() -> list[Line]:
    return [
        Line(ALICE_AT, ALICE.id, "the pass is closed", "the pass is closed", 4000),
        Line(BASE_MS + 6_000, ALICE.id, "valley lore", "valley lore", 5000, "off_topic"),
        Line(HEARD_AT, BOB.id, "we clime", "we clime", 3000),
    ]


class FixBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.store_t = TranscriptStore(self.db)
        self.sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START + 86_400)
        await self.store_t.add_lines(THURSDAY.id, self.sid, lines())
        await self.store_t.end_session(THURSDAY.id, self.sid, START + 90_000)
        self.bob = await self.session_of(BOB)
        self.carol = await self.session_of(CAROL)
        async with self.db.guild(THURSDAY.id) as conn:
            for user in (ALICE.id, BOB.id):
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (THURSDAY.id, user, NOW, TERMS_VERSION),
                )
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = 77 WHERE id = %s", (self.one.id,)
            )

    async def session_of(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def ask(
        self,
        who: Any,
        words: str = "we climb",
        *,
        speaker: Any = BOB,
        at: int = HEARD_AT,
        ref: str | None = None,
    ) -> dict[str, str]:
        return await line_fixes.request(
            self.web, who, ref or self.cref, 1, str(speaker.id), at, words,
            now=NOW, enforce_plans=False,
        )  # fmt: skip

    async def refused(self, who: Any, words: str = "we climb", **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.ask(who, words, **kw)
        return str(caught.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id, speaker, mode, words, state, stage FROM web_line_fixes"
                " ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def line_words(self, at: int, speaker: int) -> tuple[str, str | None]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT heard, text FROM transcript_lines WHERE session_id = %s"
                " AND user_id = %s AND started_ms = %s",
                (self.sid, speaker, at),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["heard"], row["text"]


class Who(FixBase):
    async def test_the_speaker_suggests_their_own_line(self) -> None:
        got = await self.ask(self.bob)
        self.assertEqual(got["mode"], "suggest")
        (row,) = await self.rows()
        self.assertEqual(
            (row["user_id"], row["speaker"], row["mode"], row["state"], row["words"]),
            (BOB.id, BOB.id, "suggest", "waiting", "we climb"),
        )

    async def test_a_dm_rewrites_any_spoken_line_of_the_campaign(self) -> None:
        got = await self.ask(self.session)
        self.assertEqual(got["mode"], "rewrite")

    async def test_a_dm_who_is_also_the_speaker_takes_the_rewrite_path(self) -> None:
        got = await self.ask(self.session, "the pass is shut", speaker=ALICE, at=ALICE_AT)
        self.assertEqual(got["mode"], "rewrite")

    async def test_someone_elses_line_is_not_yours(self) -> None:
        self.assertEqual(await self.refused(self.carol), "not_yours")
        self.assertEqual(await self.refused(self.bob, "x", speaker=ALICE, at=ALICE_AT), "not_yours")
        self.assertEqual(await self.rows(), [])

    async def test_a_dm_of_another_campaign_is_not_a_dm_here(self) -> None:
        # Bob DMs "Two" and speaks in "One": for Alice's line he is only a player.
        self.assertEqual(await self.refused(self.bob, "x", speaker=ALICE, at=ALICE_AT), "not_yours")

    async def test_another_campaigns_session_is_not_found(self) -> None:
        other = f"{THURSDAY.id}.{self.two.id}"
        with self.assertRaises(RecapError) as caught:
            await self.ask(self.bob, ref=other)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_server_the_person_is_not_in_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await self.ask(self.bob, ref=f"999.{self.one.id}")
        self.assertEqual(caught.exception.code, "not_found")


class Lines(FixBase):
    async def test_a_line_that_is_not_there_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.bob, at=HEARD_AT + 1), "not_found")

    async def test_a_dm_screen_line_cannot_be_fixed(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET sidebar = 'question' WHERE session_id = %s"
                " AND user_id = %s",
                (self.sid, BOB.id),
            )
        self.assertEqual(await self.refused(self.session), "not_found")

    async def test_a_typed_line_cannot_be_fixed(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET typed = TRUE WHERE session_id = %s AND user_id = %s",
                (self.sid, BOB.id),
            )
        self.assertEqual(await self.refused(self.bob), "not_found")

    async def test_a_deleted_line_cannot_be_fixed(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM transcript_lines WHERE user_id = %s", (BOB.id,))
        self.assertEqual(await self.refused(self.session), "not_found")

    async def test_a_speaker_who_stopped_being_recorded_is_refused(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM consent WHERE user_id = %s", (BOB.id,))
        self.assertEqual(await self.refused(self.session), "not_recorded")
        self.assertEqual(await self.refused(self.bob), "not_recorded")


class Words(FixBase):
    async def test_empty_and_too_long_words_are_refused(self) -> None:
        self.assertEqual(await self.refused(self.bob, "   "), "bad_text")
        self.assertEqual(await self.refused(self.bob, "x" * 201), "bad_text")
        self.assertEqual(await self.refused(self.bob, "‮​"), "bad_text")

    async def test_two_hundred_characters_is_allowed(self) -> None:
        await self.ask(self.bob, "y" * 200)

    async def test_the_lines_own_words_are_the_same(self) -> None:
        self.assertEqual(await self.refused(self.bob, "we  clime"), "same")
        self.assertEqual(await self.refused(self.session, " we clime "), "same")

    async def test_control_characters_are_taken_out_before_they_are_kept(self) -> None:
        await self.ask(self.bob, "we‮ climb​\n now")
        (row,) = await self.rows()
        self.assertEqual(row["words"], "we climb now")


class Suggesting(FixBase):
    async def test_the_same_words_while_one_is_open_get_the_same_one(self) -> None:
        first = await self.ask(self.bob)
        second = await self.ask(self.bob)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(await self.rows()), 1)

    async def test_other_words_for_a_line_with_one_waiting_are_open(self) -> None:
        await self.ask(self.bob)
        self.assertEqual(await self.refused(self.bob, "we climb up"), "open")

    async def test_a_suggestion_already_waiting_for_the_dm_is_open(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO transcript_suggestions (guild_id, campaign_id, session_id,"
                " started_ms, user_id, suggested, base_text, created_at)"
                " VALUES (%s, %s, %s, %s, %s, 'we climb', 'we clime', %s)",
                (THURSDAY.id, self.one.id, self.sid, HEARD_AT, BOB.id, NOW),
            )
        self.assertEqual(await self.refused(self.bob), "open")

    async def test_three_waiting_is_the_most(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for n in range(3):
                await conn.execute(
                    "INSERT INTO transcript_suggestions (guild_id, campaign_id, session_id,"
                    " started_ms, user_id, suggested, base_text, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, 'x', 'y', %s)",
                    (THURSDAY.id, self.one.id, self.sid, 5 + n, BOB.id, NOW),
                )
        self.assertEqual(await self.refused(self.bob), "many")

    async def test_a_campaign_with_no_dm_screen_cannot_take_a_suggestion(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = NULL WHERE id = %s", (self.one.id,)
            )
        self.assertEqual(await self.refused(self.bob), "no_dm_screen")
        await self.ask(self.session)  # (a DM's rewrite needs none)


class Queue(FixBase):
    async def test_twenty_waiting_in_a_campaign_is_a_flood(self) -> None:
        for n in range(20):
            await self.ask(self.session, f"words {n}")
        self.assertEqual(await self.refused(self.session, "one more"), "queue_full")

    async def test_the_website_changes_no_line(self) -> None:
        await self.ask(self.bob)
        await self.ask(self.session, "the pass is shut", speaker=ALICE, at=ALICE_AT)
        self.assertEqual(await self.line_words(HEARD_AT, BOB.id), ("we clime", None))
        self.assertEqual(await self.line_words(ALICE_AT, ALICE.id), ("the pass is closed", None))

    async def test_the_role_cannot_read_or_write_the_queue_directly(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for table in ("transcript_lines", "web_line_fixes", "transcript_suggestions"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', %s, 'SELECT') AS r,"
                    " has_table_privilege('dmbot_web', %s, 'INSERT') AS w",
                    (table, table),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual((row["r"], row["w"]), (False, False), table)

    async def test_the_words_must_be_blanked_when_a_fix_finishes(self) -> None:
        await self.ask(self.bob)
        async with self.db.guild(THURSDAY.id) as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE web_line_fixes SET state = 'done', stage = 'done', result = 'sent'"
                    )
                self.fail("a finished fix kept the person's words")
            except Exception as err:  # the table's own CHECK
                self.assertIn("check", str(err).lower())
            await conn.execute(
                "UPDATE web_line_fixes SET state = 'done', stage = 'done', result = 'sent',"
                " words = ''"
            )
        (row,) = await self.rows()
        self.assertEqual((row["state"], row["words"]), ("done", ""))


class Status(FixBase):
    async def test_a_waiting_fix_reports_the_shared_shape(self) -> None:
        made = await self.ask(self.bob)
        got = await line_fixes.status(
            self.web, self.bob, self.cref, 1, made["id"], now=NOW, enforce_plans=False,
            clock=NOW,
        )  # fmt: skip
        self.assertEqual((got["stage"], got["state"], got["result"]), ("waiting", "running", None))
        self.assertEqual(got["mode"], "suggest")
        self.assertNotIn("we climb", str(got))

    async def test_a_finished_fix_says_what_was_done_and_whether_discord_can_undo_it(self) -> None:
        made = await self.ask(self.session)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_fixes SET state = 'done', stage = 'done', result = 'changed',"
                " undo = TRUE, words = ''"
            )
        got = await line_fixes.status(
            self.web, self.session, self.cref, 1, made["id"], now=NOW, enforce_plans=False,
            clock=NOW,
        )  # fmt: skip
        self.assertEqual((got["state"], got["result"], got["undo"]), ("done", "changed", True))

    async def test_a_failure_says_why_in_a_code(self) -> None:
        made = await self.ask(self.session)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_fixes SET state = 'failed', stage = 'failed',"
                " error = 'secret', words = ''"
            )
        got = await line_fixes.status(
            self.web, self.session, self.cref, 1, made["id"], now=NOW, enforce_plans=False,
            clock=NOW,
        )  # fmt: skip
        self.assertEqual((got["state"], got["reason"], got["result"]), ("failed", "secret", None))

    async def test_nobody_reads_someone_elses_fix(self) -> None:
        made = await self.ask(self.bob)
        for other in (self.session, self.carol):
            with self.assertRaises(RecapError) as caught:
                await line_fixes.status(
                    self.web, other, self.cref, 1, made["id"], now=NOW, enforce_plans=False
                )
            self.assertEqual(caught.exception.code, "not_found")

    async def test_a_bad_id_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await line_fixes.status(
                self.web, self.bob, self.cref, 1, "nope", now=NOW, enforce_plans=False
            )
        self.assertEqual(caught.exception.code, "not_found")


class ThePage(FixBase):
    async def page(self, who: Any) -> dict[str, Any]:
        return await transcripts.read_session(
            self.web, who, self.cref, 1, "cleaned", now=NOW, enforce_plans=False
        )

    async def test_a_player_may_fix_only_their_own_spoken_lines(self) -> None:
        page = await self.page(self.bob)
        self.assertEqual(page["viewer"], {"id": str(BOB.id), "isDm": False})
        (one,) = page["fixable"]
        self.assertEqual((one["speaker"], one["startedMs"]), (str(BOB.id), HEARD_AT))
        self.assertIn("we clime", page["lines"][one["line"]])

    async def test_a_dm_may_fix_every_spoken_line_but_not_a_skipped_note(self) -> None:
        page = await self.page(self.session)
        self.assertTrue(page["viewer"]["isDm"])
        self.assertEqual(
            sorted(x["startedMs"] for x in page["fixable"]), sorted([ALICE_AT, HEARD_AT])
        )
        for x in page["fixable"]:
            self.assertNotIn("skipped", page["lines"][x["line"]])

    async def test_someone_who_never_spoke_and_is_no_dm_may_fix_nothing(self) -> None:
        page = await self.page(self.carol)
        self.assertEqual((page["viewer"]["isDm"], page["fixable"]), (False, []))

    async def test_the_list_names_lines_never_their_words(self) -> None:
        page = await self.page(self.session)
        self.assertEqual({k for x in page["fixable"] for k in x}, {"line", "speaker", "startedMs"})


class TheRoutes(FixBase):
    async def client(self, who: Any, on: bool = True) -> httpx.AsyncClient:
        app = create_app(
            settings(line_fix_on=on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        return client

    def path(self, tail: str = "") -> str:
        return f"/me/campaigns/{self.cref}/transcripts/1/fix{tail}"

    def body(self, text: str = "we climb") -> dict[str, Any]:
        return {"speaker": str(BOB.id), "startedMs": HEARD_AT, "text": text}

    async def test_ask_and_poll_over_http(self) -> None:
        client = await self.client(BOB)
        made = await client.post(self.path(), json=self.body(), headers=HEADERS)
        self.assertEqual((made.status_code, made.json()["mode"]), (202, "suggest"), made.text)
        status = await client.get(self.path(f"/{made.json()['answer']}"))
        self.assertEqual((status.status_code, status.json()["stage"]), (200, "waiting"))

    async def test_refusals_have_their_own_codes(self) -> None:
        bob = await self.client(BOB)
        carol = await self.client(CAROL)
        cases = [
            (bob, self.body(" "), 400, "bad_text"),
            (bob, self.body("we clime"), 400, "same"),
            (carol, self.body(), 403, "not_yours"),
            (bob, {**self.body(), "startedMs": 5}, 404, "not_found"),
            (bob, {**self.body(), "startedMs": "5"}, 422, "bad_choice"),
            (bob, {"speaker": 5, "startedMs": 5, "text": "x"}, 422, "bad_choice"),
        ]
        for client, body, status, error in cases:
            got = await client.post(self.path(), json=body, headers=HEADERS)
            self.assertEqual((got.status_code, got.json()["error"]), (status, error), body)

    async def test_a_second_suggestion_for_the_line_is_a_conflict(self) -> None:
        bob = await self.client(BOB)
        await bob.post(self.path(), json=self.body(), headers=HEADERS)
        got = await bob.post(self.path(), json=self.body("we climb up"), headers=HEADERS)
        self.assertEqual((got.status_code, got.json()["error"]), (409, "open"))

    async def test_the_flood_guard_is_a_429(self) -> None:
        alice = await self.client(ALICE)
        for n in range(20):
            await alice.post(self.path(), json=self.body(f"words {n}"), headers=HEADERS)
        got = await alice.post(self.path(), json=self.body("one more"), headers=HEADERS)
        self.assertEqual((got.status_code, got.json()["error"]), (429, "queue_full"))

    async def test_someone_elses_answer_is_not_found(self) -> None:
        bob = await self.client(BOB)
        made = await bob.post(self.path(), json=self.body(), headers=HEADERS)
        alice = await self.client(ALICE)
        got = await alice.get(self.path(f"/{made.json()['answer']}"))
        self.assertEqual(got.status_code, 404)

    async def test_while_switched_off_every_route_says_not_found(self) -> None:
        client = await self.client(BOB, on=False)
        for method, path in (("POST", self.path()), ("GET", self.path(f"/{'a' * 32}"))):
            got = await client.request(method, path, json=self.body(), headers=HEADERS)
            self.assertEqual((got.status_code, got.json()["error"]), (404, "not_found"), path)
        self.assertEqual(await self.rows(), [])
