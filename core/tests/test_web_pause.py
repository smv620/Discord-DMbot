"""Pause and unpause from the website (#2985): only the campaign's owner can queue it, a game
master who is not the owner is told so, a stranger gets nothing, a repeat is the same row, and the
switch keeps the route closed until the bot can apply it. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from dmbot.web import campaign_settings, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

CARL = 3003  # a game master who does not own the campaign


class PauseBase(Base):
    def cref(self) -> str:
        return f"{THURSDAY.id}.{self.one.id}"

    async def queue(self, body: object, session: Any = None, on: bool = True) -> str:
        return await campaign_settings.queue_setting(
            self.web, session or self.session, self.cref(), body, pause_on=on
        )

    async def refused(self, body: object, session: Any = None, on: bool = True) -> str:
        with self.assertRaises(RecapError) as err:
            await self.queue(body, session, on)
        return str(err.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT setting, value, turn_on, user_id, state FROM setting_actions ORDER BY seq"
            )
            return list(await cur.fetchall())

    def carl(self) -> Any:
        return SimpleNamespace(id=CARL, name="Carl", email="carl@example.com")

    async def second_dm(self) -> Any:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, CARL)
        token = await sessions.sign_in(self.web, self.carl(), [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheDoor(PauseBase):
    async def test_the_owner_queues_a_pause_and_an_unpause(self) -> None:
        first = await self.queue({"setting": "pause", "on": True})
        second = await self.queue({"setting": "pause", "on": False})
        self.assertNotEqual(first, second)
        rows = await self.rows()
        self.assertEqual(
            [(r["setting"], r["value"], r["turn_on"], r["user_id"]) for r in rows],
            [("pause", "", True, ALICE.id), ("pause", "", False, ALICE.id)],
        )

    async def test_a_second_press_gives_back_the_open_choice(self) -> None:
        a = await self.queue({"setting": "pause", "on": True})
        b = await self.queue({"setting": "pause", "on": True})
        self.assertEqual(a, b)
        self.assertEqual(len(await self.rows()), 1)

    async def test_a_game_master_who_is_not_the_owner_is_told_so(self) -> None:
        carl = await self.second_dm()
        self.assertEqual(await self.refused({"setting": "pause", "on": True}, carl), "not_owner")
        self.assertEqual(await self.rows(), [])

    async def test_a_player_or_another_campaigns_dm_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        self.assertEqual(await self.refused({"setting": "pause", "on": True}, bob), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_a_choice_without_a_yes_or_no_is_refused(self) -> None:
        for body in ({"setting": "pause"}, {"setting": "pause", "on": "yes"}):
            self.assertEqual(await self.refused(body), "bad_value")

    async def test_ten_open_at_once_is_the_most(self) -> None:
        for n in range(10):
            await self.sql_row(n)
        self.assertEqual(await self.refused({"setting": "pause", "on": True}), "too_soon")

    async def sql_row(self, n: int) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value,"
                " created_at) VALUES (%s, %s, %s, %s, 'level', 'quiet', %s)",
                (THURSDAY.id, self.one.id, f"{n:032x}", ALICE.id, NOW),
            )

    async def test_a_repeat_press_still_gets_the_open_choice_when_ten_are_open(self) -> None:
        first = await self.queue({"setting": "pause", "on": True})
        for n in range(9):
            await self.sql_row(n)
        self.assertEqual(await self.queue({"setting": "pause", "on": True}), first)
        self.assertEqual(await self.refused({"setting": "pause", "on": False}), "too_soon")

    async def test_the_table_keeps_turn_on_for_exactly_the_yes_or_no_settings(self) -> None:
        for setting, value, turn_on in (("pause", "", None), ("level", "quiet", True)):
            with self.subTest(setting=setting), self.assertRaises(Exception):  # noqa: B017
                async with self.db.guild(THURSDAY.id) as conn:
                    await conn.execute(
                        "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting,"
                        " value, turn_on, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            THURSDAY.id,
                            self.one.id,
                            "f" * 32,
                            ALICE.id,
                            setting,
                            value,
                            turn_on,
                            NOW,
                        ),
                    )

    async def test_the_switch_keeps_it_closed_before_the_body_is_judged(self) -> None:
        self.assertEqual(
            await self.refused({"setting": "pause", "on": True}, on=False), "not_found"
        )
        self.assertEqual(await self.refused({"setting": "pause"}, on=False), "not_found")
        self.assertEqual(await self.rows(), [])


class TheRoute(PauseBase):
    async def client(self, switched_on: bool, user: Any = ALICE) -> httpx.AsyncClient:
        app = create_app(
            settings(pause_on=switched_on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        c.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(c.aclose)
        return c

    async def test_a_pause_is_queued_over_http_and_polled(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        made = await c.post(base, json={"setting": "pause", "on": True}, headers=HEADERS)
        self.assertEqual(made.status_code, 202, made.text)
        got = await c.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "pause"))

    async def test_the_refusals_have_their_own_codes(self) -> None:
        await self.second_dm()
        c = await self.client(True, self.carl())
        base = f"/me/campaigns/{self.cref()}/settings"
        got = await c.post(base, json={"setting": "pause", "on": True}, headers=HEADERS)
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_owner"}))
        mine = await self.client(True)
        bad = await mine.post(base, json={"setting": "pause", "on": "no"}, headers=HEADERS)
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_value"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        c = await self.client(False)
        base = f"/me/campaigns/{self.cref()}/settings"
        got = await c.post(base, json={"setting": "pause", "on": True}, headers=HEADERS)
        self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}))
        self.assertEqual(await self.rows(), [])
