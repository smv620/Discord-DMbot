"""A campaign's session notes on the website (#3027): a DM-only list of the sessions that have
notes and one session's numbered beats, in words only. A player, another campaign's DM and a
stranger get nothing; notes of a session whose transcript was deleted still show."""

from __future__ import annotations

from typing import Any

import httpx
from psycopg.types.json import Jsonb

from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import session_notes, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

START = 1_700_000_000
SHEET = [
    {
        "what": "The party crosses the river",
        "who": ["Cerric", "Mira"],
        "lines": [3, 4],
        "kind": "travel",
        "where": "The Brass Ford",
        "note": "THE FERRYMAN IS THE HIDDEN KING",
        "keep": True,
    },
    {"what": "Cerric swears an oath", "who": ["Cerric"], "lines": [9], "kind": "talk"},
]


class Notes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.transcripts = TranscriptStore(self.db)
        self.first = await self.session_with_lines(START)
        await self.keep(self.one.id, START, SHEET)
        await self.keep(self.one.id, START + 20_000, SHEET[:1])  # a session with no transcript
        await self.keep(self.two.id, START, [{"what": "OTHER CAMPAIGN", "who": [], "lines": [],
                                              "kind": "talk"}])  # fmt: skip

    async def session_with_lines(self, at: int) -> str:
        sid = await self.transcripts.open_session(THURSDAY.id, self.one.id, at)
        await self.transcripts.add_lines(
            THURSDAY.id, sid, [Line(at * 1000 + 1000, 1, "we cross the river", "we cross")]
        )
        await self.transcripts.end_session(THURSDAY.id, sid, at + 3600)
        return sid

    async def keep(self, campaign_id: str, at: int, sheet: list[Any]) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (THURSDAY.id, campaign_id, at, Jsonb(sheet), at + 100),
            )

    async def signed_in(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def refused(self, who: Any, ref: str | None = None, at: int = START) -> str:
        with self.assertRaises(RecapError) as caught:
            await session_notes.read_notes(self.web, who, ref or self.cref, at)
        return caught.exception.code


class TheList(Notes):
    async def test_the_sessions_with_notes_come_newest_first_with_their_numbers(self) -> None:
        got = (await session_notes.read_notes_list(self.web, self.session, self.cref))["sessions"]
        self.assertEqual([s["sessionAt"] for s in got], [START + 20_000, START])
        newest, older = got
        self.assertEqual((older["sessionNumber"], older["beats"], older["writtenAt"]),
                         (1, 2, START + 100))  # fmt: skip
        self.assertEqual((newest["sessionNumber"], newest["beats"]), (None, 1))

    async def test_only_this_campaigns_notes_are_listed(self) -> None:
        got = await session_notes.read_notes_list(self.web, self.session, self.cref)
        self.assertNotIn("OTHER CAMPAIGN", repr(got))
        self.assertEqual(len(got["sessions"]), 2)

    async def test_the_notes_of_a_session_whose_transcript_was_deleted_still_show(self) -> None:
        await self.transcripts.delete_session_transcript(
            THURSDAY.id, self.one.id, self.first, 10**10, 500
        )
        got = (await session_notes.read_notes_list(self.web, self.session, self.cref))["sessions"]
        self.assertEqual([s["sessionAt"] for s in got], [START + 20_000, START])
        shown = await session_notes.read_notes(self.web, self.session, self.cref, START)
        self.assertEqual(len(shown["beats"]), 2)


class OneSession(Notes):
    async def test_the_beats_are_numbered_with_their_words_only(self) -> None:
        got = await session_notes.read_notes(self.web, self.session, self.cref, START)
        self.assertEqual((got["sessionAt"], got["sessionNumber"], got["writtenAt"]),
                         (START, 1, START + 100))  # fmt: skip
        self.assertEqual(
            got["beats"],
            [
                {
                    "number": 1,
                    "what": "The party crosses the river",
                    "who": ["Cerric", "Mira"],
                    "where": "The Brass Ford",
                    "kind": "travel",
                },
                {
                    "number": 2,
                    "what": "Cerric swears an oath",
                    "who": ["Cerric"],
                    "where": "",
                    "kind": "talk",
                },
            ],
        )

    async def test_it_never_carries_a_line_pointer_or_plotbots_note(self) -> None:
        text = repr(await session_notes.read_notes(self.web, self.session, self.cref, START))
        for word in ("THE FERRYMAN IS THE HIDDEN KING", "lines", "keep", "note"):
            self.assertNotIn(word, text)

    async def test_a_damaged_entry_is_left_out_and_the_rest_renumbered(self) -> None:
        await self.keep(
            self.one.id,
            START + 40_000,
            [{"what": 5}, "nonsense", SHEET[1]],  # (neither is a beat; the third is)
        )
        got = await session_notes.read_notes(self.web, self.session, self.cref, START + 40_000)
        self.assertEqual([(b["number"], b["what"]) for b in got["beats"]],
                         [(1, "Cerric swears an oath")])  # fmt: skip

    async def test_a_session_with_no_notes_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await session_notes.read_notes(self.web, self.session, self.cref, START + 999)
        self.assertEqual(caught.exception.code, "not_found")


class WhoMayAsk(Notes):
    async def test_a_player_another_campaigns_dm_and_a_stranger_are_refused(self) -> None:
        bob = await self.signed_in(BOB)  # Bob DMs "Two", and only plays in "One"
        self.assertEqual(await self.refused(bob), "not_allowed")
        with self.assertRaises(RecapError) as caught:
            await session_notes.read_notes_list(self.web, bob, self.cref)
        self.assertEqual(caught.exception.code, "not_allowed")
        self.assertEqual(
            await self.refused(self.session, f"{THURSDAY.id}.{self.two.id}"), "not_allowed"
        )
        self.assertEqual(
            await self.refused(self.session, f"{THURSDAY.id}.{'0' * 32}"), "not_allowed"
        )

    async def test_a_server_the_person_is_not_in_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.session, f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(self.session, "nonsense"), "not_found")

    async def test_the_doors_answer_nothing_to_anyone_who_is_not_a_dm_even_by_sql(self) -> None:
        async with self.web.user(BOB.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_session_notes_list(%s, %s) AS a,"
                " dmbot_web_session_notes(%s, %s, %s) AS b",
                (THURSDAY.id, self.one.id, THURSDAY.id, self.one.id, START),
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual((row["a"], row["b"]), (None, None))

    async def test_the_notes_table_is_not_open_to_the_website_role(self) -> None:
        async with self.web.user(ALICE.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM session_notes")


class TheRoutes(Notes):
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

    async def test_a_dm_gets_the_list_and_a_session_and_a_player_gets_not_allowed(self) -> None:
        async with await self.client(ALICE) as client:
            listed = await client.get(f"/me/campaigns/{self.cref}/session-notes")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()["sessions"]), 2)
            one = await client.get(f"/me/campaigns/{self.cref}/session-notes/{START}")
            self.assertEqual(one.status_code, 200)
            self.assertEqual(one.json()["beats"][0]["number"], 1)
            missing = await client.get(f"/me/campaigns/{self.cref}/session-notes/{START + 1}")
            self.assertEqual(missing.status_code, 404)
            gone = await client.get(f"/me/campaigns/999.{self.one.id}/session-notes")
            self.assertEqual(gone.status_code, 404)
        async with await self.client(BOB) as client:
            for path in ("session-notes", f"session-notes/{START}"):
                refused = await client.get(f"/me/campaigns/{self.cref}/{path}")
                self.assertEqual(
                    (refused.status_code, refused.json()["error"]), (403, "not_allowed")
                )
