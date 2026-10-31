"""Main rules and "If missing" from the website (#2985): a DM queues the pair through the settings
queue, only the words Discord offers are accepted (and the fallback may not be the main rules), the
switch keeps the route closed until the bot can apply it, a player or a stranger gets nothing, and
the campaign page gives the pair to a DM only. The SQL list is kept in step with the code's."""

from __future__ import annotations

import re
from typing import Any

import httpx

from dmbot.campaigns.models import FALLBACK_NONE, RULESETS
from dmbot.migrations import _MODULES
from dmbot.web import campaign_page, campaign_settings, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class RulesBase(Base):
    def cref(self) -> str:
        return f"{THURSDAY.id}.{self.one.id}"

    async def queue(self, value: object, session: Any = None, on: bool = True) -> str:
        body = {"setting": "rulesets", "value": value}
        return await campaign_settings.queue_setting(
            self.web, session or self.session, self.cref(), body, rulesets_on=on
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


class TheDoor(RulesBase):
    async def test_every_pair_discord_offers_is_queued(self) -> None:
        pairs = [
            f"{main}/{fallback}"
            for main in RULESETS
            for fallback in (*RULESETS, FALLBACK_NONE)
            if fallback != main
        ]
        self.assertGreaterEqual(len(pairs), 3)
        for pair in pairs:
            with self.subTest(pair=pair):
                await self.queue(pair)
                async with self.db.guild(THURSDAY.id) as conn:
                    await conn.execute("UPDATE setting_actions SET state = 'done'")
        rows = await self.rows()
        self.assertEqual([r["value"] for r in rows], pairs)
        self.assertEqual({(r["setting"], r["user_id"]) for r in rows}, {("rulesets", ALICE.id)})

    async def test_anything_else_is_refused(self) -> None:
        for bad in (
            "2024/2024",  # the fallback cannot be the main rules
            "2014/2014",
            "none/2024",  # "none" is only ever a fallback
            "1999/2024",
            "2024/1999",
            "2024",
            "2024/2014/none",
            "2024,2014",
            "",
            None,
            5,
        ):
            with self.subTest(bad=bad):
                self.assertEqual(await self.refused(bad), "bad_value")
        self.assertEqual(await self.rows(), [])

    async def test_the_database_door_agrees_with_the_code_on_every_pair(self) -> None:
        words = (*RULESETS, FALLBACK_NONE, "1999", "")
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for main in words:
                for fallback in words:
                    value = f"{main}/{fallback}"
                    try:
                        campaign_settings._rulesets({"value": value})
                        expected = "ok"
                    except RecapError:
                        expected = "bad_value"
                    cur = await conn.execute(
                        "SELECT dmbot_web_setting_add(%s, %s, 'rulesets', %s, false) AS r",
                        (THURSDAY.id, self.one.id, value),
                    )
                    row = await cur.fetchone()
                    assert row is not None
                    got = row["r"]["status"]
                    self.assertEqual(got if got == "bad_value" else "ok", expected, value)
                    if got == "too_soon":  # (ten open at once is the most: stop before it matters)
                        return

    def test_the_sql_list_is_the_codes_list(self) -> None:
        sql = next(m.SQL for m in _MODULES if m.__name__.endswith("0297_rulesets_setting"))
        found = re.search(r"\^\(([0-9|]+)\)/\(([0-9a-z|]+)\)\$", sql)
        assert found is not None
        self.assertEqual(set(found.group(1).split("|")), set(RULESETS))
        self.assertEqual(set(found.group(2).split("|")), {*RULESETS, FALLBACK_NONE})

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        self.assertEqual(await self.refused("2024/2014", bob), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_a_dm_cannot_queue_rules_for_a_campaign_they_do_not_run(self) -> None:
        other = f"{THURSDAY.id}.{self.two.id}"  # (Bob runs this one, Alice does not)
        with self.assertRaises(RecapError) as err:
            await campaign_settings.queue_setting(
                self.web,
                self.session,
                other,
                {"setting": "rulesets", "value": "2024/2014"},
                rulesets_on=True,
            )
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_the_switch_keeps_it_closed_before_the_body_is_judged(self) -> None:
        self.assertEqual(await self.refused("2024/2014", on=False), "not_found")
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
            {"setting": "dndbeyond_invite", "value": ""},
            invite_on=True,
        )
        self.assertEqual(
            [(r["setting"], r["value"]) for r in await self.rows()],
            [("visibility", "peek"), ("dndbeyond_invite", "")],
        )

    async def test_the_campaign_page_gives_the_pair_to_a_dm_only(self) -> None:
        page = await campaign_page.read_campaign(self.web, self.session, self.cref())
        self.assertEqual(
            (page["settings"]["mainRules"], page["settings"]["fallbackRules"]), ("2024", "2014")
        )
        who: Any = BOB
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None
        as_player = await campaign_page.read_campaign(self.web, bob, self.cref())
        self.assertNotIn("settings", as_player)


class TheRoute(RulesBase):
    async def client(self, switched_on: bool) -> httpx.AsyncClient:
        app = create_app(
            settings(rulesets_on=switched_on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        c.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(c.aclose)
        return c

    async def test_a_pair_is_queued_over_http_and_polled(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        made = await c.post(
            base, json={"setting": "rulesets", "value": "2014/2024"}, headers=HEADERS
        )
        self.assertEqual(made.status_code, 202, made.text)
        got = await c.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "rulesets"))

    async def test_a_bad_pair_is_a_400_with_its_own_word(self) -> None:
        c = await self.client(True)
        base = f"/me/campaigns/{self.cref()}/settings"
        bad = await c.post(
            base, json={"setting": "rulesets", "value": "2024/2024"}, headers=HEADERS
        )
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_value"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        c = await self.client(False)
        base = f"/me/campaigns/{self.cref()}/settings"
        got = await c.post(
            base, json={"setting": "rulesets", "value": "2024/2014"}, headers=HEADERS
        )
        self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}))
        self.assertEqual(await self.rows(), [])
