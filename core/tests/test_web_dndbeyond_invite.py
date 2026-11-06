"""The D&D Beyond invite link from the website (#2985): a DM sets, changes or removes it through the
settings queue, only the one standard shape is accepted (the same parser as Discord's form), the
switch keeps the route closed until the bot can apply it, a player or stranger gets nothing, and the
campaign page gives the link to a DM only. Invented codes."""

from __future__ import annotations

from typing import Any

import httpx
import psycopg.errors

from dmbot.web import campaign_page, campaign_settings, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

CODE = "AbC123xyz"
STANDARD = f"https://www.dndbeyond.com/campaigns/join/{CODE}"


class InviteBase(Base):
    def cref(self) -> str:
        return f"{THURSDAY.id}.{self.one.id}"

    async def queue(self, value: object, session: Any = None, on: bool = True) -> str:
        body = {"setting": "dndbeyond_invite", "value": value}
        return await campaign_settings.queue_setting(
            self.web, session or self.session, self.cref(), body, invite_on=on
        )

    async def refused(self, value: object, session: Any = None, on: bool = True) -> str:
        with self.assertRaises(RecapError) as err:
            await self.queue(value, session, on)
        return str(err.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT setting, value, user_id FROM setting_actions ORDER BY seq"
            )
            return list(await cur.fetchall())


class TheDoor(InviteBase):
    async def test_a_link_is_queued_in_its_standard_form(self) -> None:
        for given in (STANDARD, f"https://dndbeyond.com/campaigns/join/{CODE}", f"  {STANDARD}/ "):
            with self.subTest(given=given):
                await self.queue(given)
        rows = await self.rows()
        self.assertEqual({r["value"] for r in rows}, {STANDARD})
        self.assertEqual(
            {(r["setting"], r["user_id"]) for r in rows}, {("dndbeyond_invite", ALICE.id)}
        )

    async def test_an_empty_box_queues_a_removal(self) -> None:
        await self.queue("")
        await self.queue("   ")
        self.assertEqual([r["value"] for r in await self.rows()], ["", ""])

    async def test_anything_but_the_one_shape_is_refused(self) -> None:
        for bad in (
            "http://www.dndbeyond.com/campaigns/join/AbC123xyz",
            "https://dndbeyond.com.evil.example/campaigns/join/AbC123xyz",
            "https://www.dndbeyond.com/campaigns/AbC123xyz",
            "https://www.dndbeyond.com/campaigns/join/ab",
            f"look at {STANDARD}",
            f"{STANDARD}?utm=x",
            "x" * 300,
            None,
            5,
        ):
            with self.subTest(bad=bad):
                self.assertEqual(await self.refused(bad), "bad_value")
        self.assertEqual(await self.rows(), [])

    async def test_the_door_refuses_what_the_page_check_missed(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for value, status in (
                (STANDARD, "ok"),
                ("", "ok"),
                ("https://evil.example/campaigns/join/AbC123xyz", "bad_value"),
                (f"{STANDARD}/", "bad_value"),
                ("http://www.dndbeyond.com/campaigns/join/AbC123xyz", "bad_value"),
            ):
                cur = await conn.execute(
                    "SELECT dmbot_web_setting_add(%s, %s, 'dndbeyond_invite', %s, false) AS r",
                    (THURSDAY.id, self.one.id, value),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["r"]["status"], status, value)

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        self.assertEqual(await self.refused(STANDARD, bob), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_a_dm_cannot_queue_an_invite_for_a_campaign_they_do_not_run(self) -> None:
        other = f"{THURSDAY.id}.{self.two.id}"  # (Bob runs this one, Alice does not)
        with self.assertRaises(RecapError) as err:
            await campaign_settings.queue_setting(
                self.web,
                self.session,
                other,
                {"setting": "dndbeyond_invite", "value": STANDARD},
                invite_on=True,
            )
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_the_switch_keeps_it_closed_before_the_body_is_judged(self) -> None:
        self.assertEqual(await self.refused(STANDARD, on=False), "not_found")
        self.assertEqual(await self.refused("nonsense", on=False), "not_found")
        self.assertEqual(await self.rows(), [])

    async def test_the_other_settings_still_work_through_the_replaced_door(self) -> None:
        await campaign_settings.queue_setting(
            self.web, self.session, self.cref(), {"setting": "visibility", "value": "peek"}
        )
        await campaign_settings.queue_setting(
            self.web,
            self.session,
            self.cref(),
            {"setting": "story_mode", "value": "watch", "confirm": True},
            story_mode_on=True,
        )
        self.assertEqual(
            [(r["setting"], r["value"]) for r in await self.rows()],
            [("visibility", "peek"), ("story_mode", "watch")],
        )

    async def test_a_link_is_longer_than_the_other_settings_allow_and_only_for_this_setting(
        self,
    ) -> None:
        self.assertGreater(len(STANDARD), 40)
        await self.queue(STANDARD)  # (the old limit of 40 would have refused it)
        async with self.db.guild(THURSDAY.id) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting,"
                    " value, created_at) VALUES (%s, %s, %s, %s, 'level', %s, %s)",
                    (THURSDAY.id, self.one.id, "e" * 32, ALICE.id, "x" * 41, NOW),
                )

    async def test_the_campaign_page_gives_the_invite_to_a_dm_only(self) -> None:
        page = await campaign_page.read_campaign(self.web, self.session, self.cref())
        self.assertEqual(page["settings"]["dndbeyondInvite"], "")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET dndbeyond_invite = %s WHERE guild_id = %s AND id = %s",
                (STANDARD, THURSDAY.id, self.one.id),
            )
        page = await campaign_page.read_campaign(self.web, self.session, self.cref())
        self.assertEqual(page["settings"]["dndbeyondInvite"], STANDARD)
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        as_player = await campaign_page.read_campaign(self.web, bob, self.cref())
        self.assertNotIn("settings", as_player)
        self.assertNotIn("dndbeyond", repr(as_player).lower())


class TheRoute(InviteBase):
    async def client(self, switched_on: bool) -> httpx.AsyncClient:
        app = create_app(
            settings(invite_on=switched_on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        c.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(c.aclose)
        return c

    async def test_a_link_is_queued_over_http_and_polled(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        made = await c.post(
            base, json={"setting": "dndbeyond_invite", "value": STANDARD}, headers=HEADERS
        )
        self.assertEqual(made.status_code, 202, made.text)
        got = await c.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "dndbeyond_invite"))

    async def test_a_bad_link_is_a_400_with_its_own_word(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        bad = await c.post(
            base, json={"setting": "dndbeyond_invite", "value": "nope"}, headers=HEADERS
        )
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_value"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        c = await self.client(False)
        base = f"/me/campaigns/{self.cref()}/settings"
        got = await c.post(
            base, json={"setting": "dndbeyond_invite", "value": STANDARD}, headers=HEADERS
        )
        self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}))
        self.assertEqual(await self.rows(), [])
