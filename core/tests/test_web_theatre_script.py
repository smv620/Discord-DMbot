"""A session's theatrical script as a download for the campaign's game masters (#3244): the file's
words, who may ask, what a session with no script says, and that the website's role reaches the
script only through its one door."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
from psycopg.types.json import Jsonb

from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_store import Row, TheatreStore
from dmbot.web import sessions, theatre_script
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

START = 1_700_000_000
STRANGER = 5555


class Script(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(THURSDAY.id, self.one.id, START)
        self.ids = await self.transcripts.add_lines(
            THURSDAY.id,
            self.sid,
            [
                Line(START * 1000 + 1000, ALICE.id, "i draw my sword", "I draw my sword."),
                Line(START * 1000 + 2000, BOB.id, "she says no", "Mira says no."),
                Line(START * 1000 + 3000, STRANGER, "the door opens", "The door opens."),
            ],
        )
        await self.transcripts.end_session(THURSDAY.id, self.sid, START + 3600)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET speaker_names = %s WHERE id = %s",
                (Jsonb({str(ALICE.id): "Belleros", str(BOB.id): "Bob"}), self.sid),
            )

    async def make_script(self) -> None:
        saved = await TheatreStore(self.db).replace(
            THURSDAY.id,
            self.sid,
            [
                Row(ALICE.id, "Mira [Belleros's character] draws her sword.", (self.ids[0],)),
                Row(BOB.id, "No,   says\nCerric [Bob's character].", (self.ids[1],)),
                Row(STRANGER, "The door [of the inn] opens.", (self.ids[2],)),
            ],
        )
        assert saved.kept == 3

    async def signed_in(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def render(
        self, who: Any | None = None, ref: str | None = None, number: int = 1
    ) -> tuple[str, str]:
        return await theatre_script.render_script(
            self.web, who or self.session, ref or self.cref, number, now=NOW, enforce_plans=False
        )

    async def refused(self, who: Any | None = None, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.render(who, **kw)
        return caught.exception.code


class TheFile(Script):
    async def test_a_dm_gets_the_script_with_names_and_a_plain_heading(self) -> None:
        await self.make_script()
        name, text = await self.render()
        self.assertEqual(name, "one-session-1-script.txt")
        self.assertTrue(text.startswith("One, session 1: the script\n"))
        self.assertIn("Only the game master should have it", text)
        body = text.split("\n\n", 2)[2]
        self.assertEqual(
            body.splitlines(),
            [
                "Belleros: Mira [Belleros's character] draws her sword.",
                "Bob: No, says Cerric [Bob's character].",  # (spaces and breaks made one space)
                "Speaker 1: The door [of the inn] opens.",  # (a name never kept)
            ],
        )

    async def test_it_holds_no_transcript_line_that_is_not_in_the_script(self) -> None:
        await self.make_script()
        _name, text = await self.render()
        for heard in ("i draw my sword", "she says no", "the door opens"):
            self.assertNotIn(heard, text)

    def test_the_file_name_is_a_safe_slug(self) -> None:
        self.assertEqual(theatre_script.file_name("Rime of the Frost-maiden!", 7),
                         "rime-of-the-frost-maiden-session-7-script.txt")  # fmt: skip
        self.assertEqual(theatre_script.file_name("???", 2), "campaign-session-2-script.txt")


class TheText(Script):
    def test_a_name_cannot_start_a_fake_line(self) -> None:
        text = theatre_script.render_text(
            "Camp\naign", 1, [{"user": "7", "text": "hi"}], {7: "Eve\nBob: sneaky"}
        )
        self.assertEqual(text.splitlines()[0], "Camp aign, session 1: the script")
        self.assertEqual(text.splitlines()[-1], "Eve Bob: sneaky: hi")


class WhoMayAsk(Script):
    async def test_a_session_with_no_script_says_so(self) -> None:
        self.assertEqual(await self.refused(), "no_script")

    async def test_a_player_and_another_campaigns_dm_are_refused_and_a_stranger_gets_nothing(
        self,
    ) -> None:
        await self.make_script()
        bob = await self.signed_in(BOB)  # Bob DMs "Two" and only plays in "One"
        self.assertEqual(await self.refused(bob), "not_allowed")
        self.assertEqual(await self.refused(ref=f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(number=9), "not_found")

    async def test_a_co_dm_may_save_it_and_another_campaigns_session_is_no_script(self) -> None:
        await self.make_script()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)",
                (self.one.id, THURSDAY.id, BOB.id),
            )
        bob = await self.signed_in(BOB)
        _name, text = await self.render(bob)
        self.assertIn("Belleros:", text)
        # Alice is not a DM of "Two": the door answers her nothing, though the session id is real
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_theatre_script(%s, %s, %s) AS r",
                (THURSDAY.id, self.two.id, self.sid),
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["r"])

    async def test_the_door_answers_nothing_to_a_non_dm_even_by_sql(self) -> None:
        await self.make_script()
        bob = await self.signed_in(BOB)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_theatre_script(%s, %s, %s) AS r",
                (THURSDAY.id, self.one.id, self.sid),
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["r"])

    async def test_the_script_table_is_not_open_to_the_website_role(self) -> None:
        await self.make_script()
        async with self.web.user(ALICE.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM theatre_lines")

    async def test_a_deleted_players_lines_leave_the_script_and_so_the_file(self) -> None:
        await self.make_script()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "DELETE FROM transcript_lines WHERE guild_id = %s AND id = %s",
                (THURSDAY.id, self.ids[1]),
            )
        _name, text = await self.render()  # (the trigger drops only the rows that cite the line)
        self.assertNotIn("Bob:", text)
        self.assertIn("Belleros:", text)


class TheRoute(Script):
    async def client(self, user: Any) -> httpx.AsyncClient:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        )  # fmt: skip
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def test_the_file_comes_as_a_download_and_the_statuses_are_right(self) -> None:
        url = f"/me/campaigns/{self.cref}/transcripts/1/script"
        async with await self.client(ALICE) as client:
            none = await client.get(url)
            self.assertEqual((none.status_code, none.json()["error"]), (404, "no_script"))
            await self.make_script()
            got = await client.get(url)
            self.assertEqual(got.status_code, 200)
            self.assertIn('filename="one-session-1-script.txt"', got.headers["content-disposition"])
            self.assertTrue(got.headers["content-type"].startswith("text/plain"))
            self.assertEqual(got.headers["cache-control"], "no-store")
            self.assertIn("Belleros: Mira", got.text)
            missing = await client.get(f"/me/campaigns/{self.cref}/transcripts/9/script")
            self.assertEqual(missing.status_code, 404)
        async with await self.client(BOB) as client:
            denied = await client.get(url)
            self.assertEqual((denied.status_code, denied.json()["error"]), (403, "not_allowed"))

    async def test_too_big_a_file_is_refused(self) -> None:
        await self.make_script()
        async with await self.client(ALICE) as client:
            with patch.object(theatre_script, "FILE_LIMIT", 50):
                got = await client.get(f"/me/campaigns/{self.cref}/transcripts/1/script")
        self.assertEqual((got.status_code, got.json()["error"]), (413, "too_big"))

    async def test_signed_out_is_401(self) -> None:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        ) as client:  # fmt: skip
            got = await client.get(f"/me/campaigns/{self.cref}/transcripts/1/script")
        self.assertEqual(got.status_code, 401)
