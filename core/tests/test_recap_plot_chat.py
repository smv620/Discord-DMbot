"""The DM's talk with PlotBot about one issue, website side (#2123): queued for the bot, read back
by the DM alone, Save or Not that on a proposal, and never anyone else's. Invented fixtures; the
bot's answer is written straight into the table, as the bot would."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from dmbot.web import sessions
from dmbot.web.discord import DiscordUser
from tests.test_recap_plot import PlotBase
from tests.test_recap_web import HEADERS, NOW
from tests.test_web_api import ALICE, API, THURSDAY

BOB = DiscordUser(id=1002, name="Rowan", email="rowan@example.com")


class Talking(PlotBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.checked()
        self.issue_id = await self.issue("a1")

    def url(self, tail: str = "chat", issue: str | None = None) -> str:
        return f"/me/recaps/{self.ref}/plot/{issue or self.issue_id}/{tail}"

    async def say(self, text: str = "It is a different Ulfgar.") -> httpx.Response:
        return await self.client.post(self.url(), json={"text": text})

    async def bot_reply(self, *proposals: dict[str, Any], user: int | None = None) -> str:
        """What the bot writes: an answer row, with its proposals; the DM's row is done."""
        mid = uuid.uuid4().hex
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET state = 'done' WHERE role = 'dm' AND issue_id = %s",
                (self.issue_id,),
            )
            await conn.execute(
                "INSERT INTO plot_chat (guild_id, campaign_id, id, issue_id, session_at, user_id,"
                " role, body, state, proposals, created_at) VALUES (%s, %s, %s, %s, 100, %s,"
                " 'bot', 'Noted.', 'done', %s::jsonb,"
                " floor(extract(epoch FROM clock_timestamp()))::BIGINT + 1)",
                (
                    THURSDAY.id,
                    self.one.id,
                    mid,
                    self.issue_id,
                    user or (await self.me_id()),
                    json.dumps(list(proposals)),
                ),
            )
        return mid

    async def me_id(self) -> int:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT user_id FROM plot_chat LIMIT 1")
            row = await cur.fetchone()
            if row:
                return int(row["user_id"])
        return int(ALICE.id)

    def proposal(self, pid: str = "p1", state: str = "open") -> dict[str, Any]:
        return {
            "id": pid,
            "kind": "fact",
            "label": "Ulfgar: alive again",
            "data": {},
            "state": state,
        }

    async def messages(self) -> list[dict[str, Any]]:
        got = await self.client.get(self.url())
        self.assertEqual(got.status_code, 200, got.text)
        return list(got.json()["messages"])

    async def test_a_message_is_queued_and_read_back_in_order(self) -> None:
        sent = await self.say()
        self.assertEqual(sent.status_code, 200, sent.text)
        [one] = await self.messages()
        self.assertEqual(
            (one["role"], one["text"], one["state"]), ("dm", "It is a different Ulfgar.", "waiting")
        )
        await self.bot_reply(self.proposal())
        got = await self.messages()
        self.assertEqual([m["role"] for m in got], ["dm", "bot"])
        self.assertEqual(got[1]["proposals"][0]["state"], "open")

    async def test_bad_words_are_refused(self) -> None:
        for text in ("", "   ", "x" * 1001):
            got = await self.say(text)
            self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_text"), text[:5])
        got = await self.client.post(self.url(), json={"text": 5})
        self.assertEqual(got.status_code, 422)

    async def test_only_one_message_waits_at_a_time(self) -> None:
        await self.say()
        again = await self.say("And one more thing.")
        self.assertEqual((again.status_code, again.json()["error"]), (409, "busy"))
        await self.bot_reply()
        self.assertEqual((await self.say("And one more thing.")).status_code, 200)

    async def test_a_talk_has_a_limit(self) -> None:
        for n in range(15):
            self.assertEqual((await self.say(f"Point {n}.")).status_code, 200)
            await self.bot_reply()
        got = await self.say("One more.")
        self.assertEqual((got.status_code, got.json()["error"]), (429, "too_soon"))

    async def test_a_changed_text_is_stale(self) -> None:
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="A different recap.")
        got = await self.say()
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stale"))

    async def test_an_unknown_issue_is_not_found(self) -> None:
        got = await self.client.post(self.url(issue="nope"), json={"text": "hi"})
        self.assertEqual(got.status_code, 404)
        self.assertEqual((await self.client.get(self.url(issue="nope"))).status_code, 404)

    async def test_not_that_closes_the_proposal_and_saves_nothing(self) -> None:
        await self.say()
        mid = await self.bot_reply(self.proposal())
        got = await self.client.post(self.url("proposals/p1"), json={"action": "decline"})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["state"], got.json()["messageId"]), ("declined", mid))
        # a proposal is closed once: a second press is refused and nothing changes
        again = await self.client.post(self.url("proposals/p1"), json={"action": "save"})
        self.assertEqual(again.status_code, 409)
        self.assertEqual((await self.messages())[-1]["proposals"][0]["state"], "declined")

    async def test_save_only_queues_it_for_the_bot(self) -> None:
        await self.say()
        await self.bot_reply(self.proposal("p1"), self.proposal("p2"))
        got = await self.client.post(self.url("proposals/p2"), json={"action": "save"})
        self.assertEqual((got.status_code, got.json()["state"]), (200, "saving"))
        states = [p["state"] for p in (await self.messages())[-1]["proposals"]]
        self.assertEqual(states, ["open", "saving"])  # the other is untouched
        answered = (await self.plot()).json()["issues"][0]["answer"]
        self.assertIsNone(answered)  # the issue is answered by the bot after it saves

    async def test_a_wrong_action_or_proposal_is_refused(self) -> None:
        await self.say()
        await self.bot_reply(self.proposal())
        bad = await self.client.post(self.url("proposals/p1"), json={"action": "maybe"})
        self.assertEqual((bad.status_code, bad.json()["error"]), (422, "bad_answer"))
        none = await self.client.post(self.url("proposals/zzz"), json={"action": "save"})
        self.assertEqual(none.status_code, 404)

    async def test_the_talk_is_the_dms_own(self) -> None:
        await self.say("A private thought.")
        async with self.db.guild(THURSDAY.id) as conn:  # a co-DM
            await conn.execute(
                "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
                (THURSDAY.id, self.one.id, BOB.id),
            )
        token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
        other = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        other.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        try:
            got = await other.get(self.url())
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual(got.json()["messages"], [])  # their own, and there is none
        finally:
            await other.aclose()

    async def test_someone_who_is_not_a_dm_is_refused(self) -> None:
        token = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
        other = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        other.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        try:
            for call in (
                other.post(self.url(), json={"text": "hi"}),
                other.get(self.url()),
                other.post(self.url("proposals/p1"), json={"action": "save"}),
            ):
                self.assertEqual((await call).status_code, 404)
        finally:
            await other.aclose()
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM plot_chat")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_another_campaigns_issue_is_not_reachable(self) -> None:
        got = await self.client.post(
            f"/me/recaps/{self.other}/plot/{self.issue_id}/chat", json={"text": "hi"}
        )
        self.assertEqual(got.status_code, 404)

    async def test_the_talk_is_not_on_the_public_recap_or_the_page(self) -> None:
        await self.say("A private thought.")
        page = (await self.plot()).text
        self.assertNotIn("private thought", page)

    async def test_the_website_role_cannot_read_the_table(self) -> None:
        import psycopg

        async with self.web.user(ALICE.id, session="x") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT * FROM plot_chat")
