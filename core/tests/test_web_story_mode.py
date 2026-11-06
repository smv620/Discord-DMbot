"""The Shared story switch from the website (#2985): a DM queues off, watch or guide, turning it on
needs the right-to-use confirmation, a stranger gets nothing, and the switch keeps the route closed
until the bot can apply it. Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.web import campaign_page, campaign_settings, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class StoryBase(Base):
    def cref(self) -> str:
        return f"{THURSDAY.id}.{self.one.id}"

    async def queue(self, body: object, session: Any = None, on: bool = True) -> str:
        return await campaign_settings.queue_setting(
            self.web, session or self.session, self.cref(), body, story_mode_on=on
        )

    async def refused(self, body: object, session: Any = None, on: bool = True) -> str:
        with self.assertRaises(RecapError) as err:
            await self.queue(body, session, on)
        return str(err.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT setting, value, flag, turn_on, user_id FROM setting_actions ORDER BY seq"
            )
            return list(await cur.fetchall())


class TheDoor(StoryBase):
    async def test_each_mode_is_queued_with_the_confirmation_kept(self) -> None:
        await self.queue({"setting": "story_mode", "value": "watch", "confirm": True})
        await self.queue({"setting": "story_mode", "value": "guide", "confirm": True})
        await self.queue({"setting": "story_mode", "value": "off"})
        rows = await self.rows()
        self.assertEqual(
            [(r["setting"], r["value"], r["flag"], r["turn_on"]) for r in rows],
            [
                ("story_mode", "watch", True, None),
                ("story_mode", "guide", True, None),
                ("story_mode", "off", False, None),
            ],
        )
        self.assertEqual({r["user_id"] for r in rows}, {ALICE.id})

    async def test_turning_it_on_without_the_confirmation_queues_nothing(self) -> None:
        for body in (
            {"setting": "story_mode", "value": "watch"},
            {"setting": "story_mode", "value": "guide", "confirm": False},
            {"setting": "story_mode", "value": "guide", "confirm": "yes"},
        ):
            with self.subTest(body=body):
                self.assertEqual(await self.refused(body), "needs_confirm")
        self.assertEqual(await self.rows(), [])

    async def test_a_mode_that_is_not_offered_is_refused(self) -> None:
        for value in ("loud", "", None, 3):
            with self.subTest(value=value):
                body = {"setting": "story_mode", "value": value, "confirm": True}
                self.assertEqual(await self.refused(body), "bad_value")
        self.assertEqual(await self.rows(), [])

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        body = {"setting": "story_mode", "value": "watch", "confirm": True}
        self.assertEqual(await self.refused(body, bob), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_the_other_settings_still_work_through_the_replaced_door(self) -> None:
        await self.queue({"setting": "visibility", "value": "peek"})
        await self.queue({"setting": "retention", "value": 90, "confirm": True})
        rows = await self.rows()
        self.assertEqual(
            [(r["setting"], r["value"], r["flag"]) for r in rows],
            [("visibility", "peek", False), ("retention", "90", True)],
        )

    async def test_the_door_keeps_its_pinned_search_path_and_is_not_public(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT proconfig, has_function_privilege('public',"
                " 'dmbot_web_setting_add(bigint, text, text, text, boolean)', 'EXECUTE') AS pub"
                " FROM pg_proc WHERE proname = 'dmbot_web_setting_add'"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["pub"])
        self.assertTrue(any("search_path" in c for c in row["proconfig"] or []))

    async def test_the_switch_keeps_it_closed_before_the_body_is_judged(self) -> None:
        good = {"setting": "story_mode", "value": "watch", "confirm": True}
        self.assertEqual(await self.refused(good, on=False), "not_found")
        self.assertEqual(await self.refused({"setting": "story_mode"}, on=False), "not_found")
        self.assertEqual(await self.rows(), [])

    async def test_the_campaign_page_reads_the_current_mode(self) -> None:
        page = await campaign_page.read_campaign(self.web, self.session, self.cref())
        self.assertEqual(page["settings"]["storyMode"], "off")


class TheRoute(StoryBase):
    async def client(self, switched_on: bool) -> httpx.AsyncClient:
        app = create_app(
            settings(story_mode_on=switched_on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        c.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(c.aclose)
        return c

    async def test_a_choice_is_queued_over_http_and_polled(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        body = {"setting": "story_mode", "value": "guide", "confirm": True}
        made = await c.post(base, json=body, headers=HEADERS)
        self.assertEqual(made.status_code, 202, made.text)
        got = await c.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "story_mode"))

    async def test_a_missing_confirmation_is_a_400_with_its_own_word(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        got = await c.post(base, json={"setting": "story_mode", "value": "watch"}, headers=HEADERS)
        self.assertEqual((got.status_code, got.json()), (400, {"error": "needs_confirm"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        c = await self.client(False)
        base = f"/me/campaigns/{self.cref()}/settings"
        body = {"setting": "story_mode", "value": "watch", "confirm": True}
        got = await c.post(base, json=body, headers=HEADERS)
        self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}))
        self.assertEqual(await self.rows(), [])
