"""The House-rules file on the website (#2985, slice 1): the DM-only doors, one open request a
campaign, the caps, the site shown without the address, isolation from another campaign and a
player, and nothing of the link or the words in what comes back. Invented names and addresses."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.web import house_file, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_report import GUILD, Reading

LINK = "https://docs.example.com/document/d/secret-token-123/edit"
WORDS = "1. Flanking gives advantage (instead of: the book's flanking)"


class TheDoors(Reading):
    async def add(self, who: Any = None, ref: str | None = None, **body: Any) -> dict[str, Any]:
        body.setdefault("action", "check")
        return await house_file.add(self.web, who or self.session, ref or self.cref, body)

    async def turned_away(self, who: Any = None, ref: str | None = None, **body: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await self.add(who, ref, **body)
        return err.exception.code

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, action, link, text, user_id FROM web_house_file_actions ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def connect_a_file(self) -> None:
        await self.sql(
            "INSERT INTO house_rules_file (campaign_id, guild_id, link, set_by, set_at)"
            " VALUES (%s, %s, %s, %s, 1)",
            self.one.id, GUILD, LINK, ALICE.id,
        )  # fmt: skip

    async def test_each_action_is_queued_with_the_choosing_dm(self) -> None:
        for body in (
            {"action": "link", "link": LINK},
            {"action": "unlink"},
            {"action": "check"},
            {"action": "upload", "text": WORDS},
        ):
            with self.subTest(body=body):
                got = await self.add(**body)
                self.assertEqual(got["request"]["state"], "waiting")
                self.assertEqual(got["request"]["action"], body["action"])
                await self.sql("UPDATE web_house_file_actions SET state = 'done'")
        rows = await self.rows()
        self.assertEqual([r["action"] for r in rows], ["link", "unlink", "check", "upload"])
        self.assertEqual({r["user_id"] for r in rows}, {ALICE.id})

    async def test_the_page_never_gets_the_address_back_only_the_site(self) -> None:
        await self.connect_a_file()
        got = await house_file.read(self.web, self.session, self.cref, None)
        self.assertEqual(
            (got["linked"], got["site"], got["request"]), (True, "docs.example.com", None)
        )
        self.assertNotIn("secret-token", repr(got))
        queued = await self.add(action="link", link=LINK)
        self.assertNotIn("secret-token", repr(queued))
        self.assertNotIn(WORDS, repr(await self.add_after_done(action="upload", text=WORDS)))

    async def add_after_done(self, **body: Any) -> dict[str, Any]:
        await self.sql("UPDATE web_house_file_actions SET state = 'done'")
        return await self.add(**body)

    async def test_with_no_file_it_says_so_and_has_no_site(self) -> None:
        got = await house_file.read(self.web, self.session, self.cref, None)
        self.assertEqual((got["linked"], got["site"], got["request"]), (False, "", None))

    async def test_bad_words_are_refused_and_nothing_is_queued(self) -> None:
        for body in (
            {"action": "burn"},
            {"action": "link"},
            {"action": "link", "link": "http://docs.example.com/plain"},
            {"action": "link", "link": "https://example.com/a b"},
            {"action": "link", "link": LINK, "text": WORDS},
            {"action": "upload", "text": "   "},
            {"action": "upload", "text": WORDS, "link": LINK},
            {"action": "check", "link": LINK},
            {"action": "unlink", "text": WORDS},
            {"action": "link", "link": 5},
            {"link": LINK},
        ):
            with self.subTest(body=body):
                self.assertIn(await self.turned_away(**body), ("bad_text", "too_long"))
        self.assertEqual(await self.rows(), [])

    async def test_a_nul_or_a_lone_surrogate_is_a_plain_refusal(self) -> None:
        for bad in ("a\x00b", "a\ud800b"):
            self.assertEqual(await self.turned_away(action="upload", text=bad), "bad_text")
        self.assertEqual(
            await self.turned_away(action="link", link="https://example.com/\x00"), "bad_text"
        )
        self.assertEqual(await self.rows(), [])

    async def test_only_one_request_is_open_and_ten_an_hour(self) -> None:
        await self.add()
        self.assertEqual(await self.turned_away(), "busy")
        for _ in range(9):
            await self.sql("UPDATE web_house_file_actions SET state = 'done'")
            await self.add()
        await self.sql("UPDATE web_house_file_actions SET state = 'done'")
        self.assertEqual(await self.turned_away(), "too_soon")
        self.assertEqual(len(await self.rows()), 10)

    async def test_a_request_nobody_took_ends_as_not_taken_and_blanks_its_words(self) -> None:
        await self.add(action="link", link=LINK)
        await self.sql(
            "UPDATE web_house_file_actions SET created_at = created_at - 2000,"
            " updated_at = updated_at - 2000"
        )
        await self.add()  # (the stale one is closed, so a new one is allowed)
        old, new = await self.rows()
        self.assertEqual((old["state"], old["link"], new["state"]), ("failed", "", "waiting"))

    async def test_reading_alone_ends_one_nobody_took_and_blanks_it(self) -> None:
        got = await self.add(action="upload", text=WORDS)
        await self.sql(
            "UPDATE web_house_file_actions SET created_at = created_at - 2000,"
            " updated_at = updated_at - 2000"
        )
        read = await house_file.read(self.web, self.session, self.cref, got["request"]["id"])
        self.assertEqual(
            (read["request"]["state"], read["request"]["error"]), ("failed", "not_taken")
        )
        [row] = await self.rows()
        self.assertEqual((row["text"], row["link"]), ("", ""))

    async def test_a_taken_request_gone_quiet_goes_back_and_gets_its_own_half_hour(self) -> None:
        await self.add()
        await self.sql(
            "UPDATE web_house_file_actions SET state = 'working', created_at = created_at - 2000,"
            " updated_at = updated_at - 700, claimed_at = 1"
        )
        read = await house_file.read(self.web, self.session, self.cref, None)
        self.assertEqual(read["request"]["state"], "waiting")  # (not failed at once)

    async def test_a_compare_shows_counts_and_no_rule(self) -> None:
        got = await self.add()
        await self.sql(
            "UPDATE web_house_file_actions SET state = 'done', stage = 'compared', to_add = 2,"
            " to_change = 1, to_remove = 3, rules_read = 12"
        )
        read = await house_file.read(self.web, self.session, self.cref, got["request"]["id"])
        r = read["request"]
        self.assertEqual((r["toAdd"], r["toChange"], r["toRemove"], r["rulesRead"]), (2, 1, 3, 12))

    async def test_only_the_campaigns_dm_can_add_or_read(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        await self.connect_a_file()
        self.assertEqual(await self.turned_away(who=bob), "not_allowed")
        got = await self.add()
        for request in (got["request"]["id"], None):
            with self.assertRaises(RecapError) as err:
                await house_file.read(self.web, bob, self.cref, request)
            self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(len(await self.rows()), 1)

    async def test_another_campaigns_request_and_file_are_not_readable_by_this_dm(self) -> None:
        await self.connect_a_file()
        got = await self.add()
        other = f"{GUILD}.{self.two.id}"  # (a campaign this DM does not run)
        for request in (got["request"]["id"], None):
            with self.assertRaises(RecapError) as err:
                await house_file.read(self.web, self.session, other, request)
            self.assertEqual(err.exception.code, "not_allowed")

    async def test_reading_a_bad_or_unknown_id_is_not_found(self) -> None:
        for bad in ("ZZ", "0" * 32):
            with self.assertRaises(RecapError) as err:
                await house_file.read(self.web, self.session, self.cref, bad)
            self.assertEqual(err.exception.code, "not_found")

    def test_the_limits_match_discords_and_the_migration(self) -> None:
        import importlib

        from dmbot.rules.house_file_link import LINK_MAX

        migration = importlib.import_module("dmbot.migrations.0295_web_house_file_actions")
        self.assertEqual(house_file.TEXT_MAX, migration.TEXT_MAX)
        self.assertEqual(house_file.LINK_MAX, LINK_MAX)

    async def test_a_file_is_taken_up_to_the_limit_and_not_past_it(self) -> None:
        await self.add(action="upload", text="x" * house_file.TEXT_MAX)
        await self.sql("UPDATE web_house_file_actions SET state = 'done'")
        self.assertEqual(
            await self.turned_away(action="upload", text="x" * (house_file.TEXT_MAX + 1)),
            "too_long",
        )


class TheRoutes(TheDoors):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(house_file_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip

    async def client(self, user: Any = ALICE, app: Any = None) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app or self.app),
            base_url="https://web-api.internal.test",
            headers={"X-DMbot-Request": "1"},
        )
        c.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(c.aclose)
        return c

    async def test_add_then_poll_over_http(self) -> None:
        c = await self.client()
        url = f"/me/campaigns/{self.cref}/house-file"
        got = await c.post(url, json={"action": "check"})
        self.assertEqual(got.status_code, 202)
        rid = got.json()["request"]["id"]
        self.assertEqual((await c.get(f"{url}/{rid}")).json()["request"]["state"], "waiting")
        self.assertEqual((await c.get(url)).json()["request"]["id"], rid)
        busy = await c.post(url, json={"action": "check"})
        self.assertEqual((busy.status_code, busy.json()), (409, {"error": "busy"}))

    async def test_refusals_over_http(self) -> None:
        c = await self.client()
        url = f"/me/campaigns/{self.cref}/house-file"
        got = await c.post(url, json={"action": "burn"})
        self.assertEqual((got.status_code, got.json()), (422, {"error": "bad_text"}))
        got = await c.post(url, json=["not", "an", "object"])
        self.assertEqual(got.status_code, 422)
        bob = await self.client(BOB)
        got = await bob.post(url, json={"action": "check"})
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        off = create_app(
            settings(), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        c = await self.client(app=off)
        url = f"/me/campaigns/{self.cref}/house-file"
        self.assertEqual((await c.post(url, json={"action": "check"})).status_code, 404)
        self.assertEqual((await c.get(url)).status_code, 404)
        self.assertEqual(await self.rows(), [])
