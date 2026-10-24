"""A campaign's past sessions with what each recap is (#2610), through the website's own database
role. A DM of the campaign gets the list, newest first; a player, another campaign's DM and a
stranger are refused. Invented names throughout.
"""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.recaps import notify
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import past_sessions, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

START = 1_700_000_000


class Past(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.transcripts = TranscriptStore(self.db)
        # Three sessions in "One": the first and the third have lines; the second has none (nothing
        # was said), so it is not a session the transcripts list shows at all.
        self.first = await self.session_with_lines(START)
        self.third = await self.session_with_lines(START + 20_000)

    async def session_with_lines(self, at: int, *, ended: int | None = 3600) -> str:
        sid = await self.transcripts.open_session(THURSDAY.id, self.one.id, at)
        await self.transcripts.add_lines(
            THURSDAY.id,
            sid,
            [Line(at * 1000 + 1000, 1, "we cross the river", "we cross the river")],
        )
        if ended is not None:
            await self.transcripts.end_session(THURSDAY.id, sid, at + ended)
        return sid

    async def listed(self, who: Any = None) -> list[dict[str, Any]]:
        got = await past_sessions.read_past_sessions(self.web, who or self.session, self.cref)
        sessions: list[dict[str, Any]] = got["sessions"]
        return sessions

    async def refused(self, who: Any, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await past_sessions.read_past_sessions(self.web, who, ref or self.cref)
        return caught.exception.code


class TheList(Past):
    async def test_the_sessions_come_newest_first_with_their_numbers_and_lengths(self) -> None:
        got = await self.listed()
        # The recap Base made sits at 100 and has no transcript session: it is listed last.
        self.assertEqual([s["sessionAt"] for s in got], [START + 20_000, START, 100])
        newest, older, odd = got
        self.assertEqual((newest["sessionNumber"], older["sessionNumber"]), (2, 1))
        self.assertEqual(newest["sessionSeconds"], 3600)
        self.assertEqual(newest["ref"], notify.job_ref(THURSDAY.id, self.one.id, START + 20_000))
        self.assertTrue(newest["hasTranscript"] and older["hasTranscript"])
        self.assertIsNone(newest["recap"])  # no recap yet: the page offers to make one
        self.assertFalse(newest["held"])
        self.assertEqual((odd["sessionNumber"], odd["hasTranscript"]), (None, False))

    async def test_a_session_with_a_recap_says_its_state(self) -> None:
        got = {s["sessionAt"]: s for s in await self.listed()}
        recap = got[100]["recap"]
        self.assertEqual(recap["state"], "ready")
        self.assertEqual(
            (recap["status"], recap["working"], recap["approved"], recap["writtenBy"]),
            ("draft", False, False, None),
        )
        self.assertFalse(recap["canMakeAgain"])  # (the key is the API's, false before #2613)

    async def test_a_session_whose_transcript_was_deleted_has_no_transcript(self) -> None:
        await self.transcripts.delete_session_transcript(
            THURSDAY.id, self.one.id, self.first, 10**10, 500
        )
        got = {s["sessionAt"]: s for s in await self.listed()}
        self.assertFalse(got[START]["hasTranscript"])  # (the recap can still be made again)
        self.assertTrue(got[START + 20_000]["hasTranscript"])

    async def test_a_recap_waiting_for_names_is_held_and_has_no_recap_yet(self) -> None:
        from dmbot.recaps.hold import HoldStore

        made = await HoldStore(self.db).hold(
            THURSDAY.id, self.one.id, START + 20_000, session_id=self.third, dm_user_id=1,
            channel_id=5, asked=True, names_open=2,
        )  # fmt: skip
        self.assertTrue(made)
        got = {s["sessionAt"]: s for s in await self.listed()}
        self.assertTrue(got[START + 20_000]["held"])
        self.assertIsNone(got[START + 20_000]["recap"])

    async def test_it_never_carries_a_word_of_a_recap_or_transcript(self) -> None:
        text = repr(await self.listed())
        for word in ("Silver Lock", "river", "Mira"):
            self.assertNotIn(word, text)


class WhoMayAsk(Past):
    async def test_a_player_another_campaigns_dm_and_a_stranger_are_refused(self) -> None:
        bob = await self.signed_in(BOB)  # Bob DMs "Two", and only plays in "One"
        self.assertEqual(await self.refused(bob), "not_allowed")
        self.assertEqual(
            await self.refused(self.session, f"{THURSDAY.id}.{self.two.id}"), "not_allowed"
        )
        self.assertEqual(
            await self.refused(self.session, f"{THURSDAY.id}.{'0' * 32}"), "not_allowed"
        )

    async def test_a_server_the_person_is_not_in_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.session, f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(self.session, "nonsense"), "not_found")

    async def test_only_this_campaigns_sessions_are_listed(self) -> None:
        await self.transcripts.open_session(THURSDAY.id, self.two.id, START + 5)
        refs = {s["ref"] for s in await self.listed()}
        self.assertTrue(all(r.split(".")[1] == self.one.id for r in refs))

    async def signed_in(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheRoute(Past):
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

    async def test_a_dm_gets_the_list_and_a_player_gets_not_allowed(self) -> None:
        async with await self.client(ALICE) as client:
            got = await client.get(f"/me/campaigns/{self.cref}/past-sessions")
            self.assertEqual(got.status_code, 200)
            self.assertEqual(len(got.json()["sessions"]), 3)
            gone = await client.get(f"/me/campaigns/999.{self.one.id}/past-sessions")
            self.assertEqual(gone.status_code, 404)
        async with await self.client(BOB) as client:
            refused = await client.get(f"/me/campaigns/{self.cref}/past-sessions")
            self.assertEqual((refused.status_code, refused.json()["error"]), (403, "not_allowed"))
