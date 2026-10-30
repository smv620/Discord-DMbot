"""Upload lore on the website (#3022): the DM-only doors, the right-to-use record, one open upload
a campaign, the caps, who can be named, isolation from other campaigns and a player, and nothing of
the words in what comes back. Invented names and ids."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.web import lore, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_report import GUILD, Reading

WORDS = "Mira was raised by the Ashen Crown."


class TheDoors(Reading):
    def body(self, **over: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": "paste",
            "text": WORDS,
            "choice": "world",
            "speaker": "",
            "rights": True,
        }
        out.update(over)
        return out

    async def add(self, who: Any = None, ref: str | None = None, **over: Any) -> dict[str, Any]:
        return await lore.add(self.web, who or self.session, ref or self.cref, self.body(**over))

    async def turned_away(self, who: Any = None, ref: str | None = None, **over: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await self.add(who, ref, **over)
        return err.exception.code

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, source, text, link, speaker, choice, rights_by, user_id"
                " FROM lore_uploads ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def test_a_paste_is_queued_with_who_confirmed_the_right_and_when(self) -> None:
        got = await self.add()
        up = got["upload"]
        self.assertEqual((up["state"], up["stage"], up["choice"]), ("waiting", "waiting", "world"))
        [row] = await self.rows()
        self.assertEqual(
            (row["text"], row["rights_by"], row["user_id"]), (WORDS, ALICE.id, ALICE.id)
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT rights_at FROM lore_uploads")
            when = await cur.fetchone()
        assert when is not None
        self.assertGreater(when["rights_at"], 1_000_000_000)

    async def test_progress_never_carries_the_words_or_the_link(self) -> None:
        got = await self.add(source="link", text="", link="https://example.com/secret-page")
        self.assertNotIn("secret-page", repr(got))
        self.assertNotIn(WORDS, repr(await lore.read(self.web, self.session, self.cref, None)))

    async def test_without_the_right_to_use_box_nothing_is_queued(self) -> None:
        self.assertEqual(await self.turned_away(rights=False), "needs_confirm")
        self.assertEqual(await self.rows(), [])

    async def test_bad_words_are_refused(self) -> None:
        for over in (
            {"source": "zip"},
            {"choice": "everything"},
            {"text": "  "},
            {"text": "x" * 4001},
            {"source": "link", "text": "", "link": "http://example.com"},
            {"source": "link", "text": "", "link": ""},
            {"source": "link", "text": WORDS, "link": "https://example.com"},
            {"link": "https://example.com"},
            {"speaker": "not-an-id"},
            {"rights": "yes"},
        ):
            with self.subTest(over=over):
                self.assertIn(await self.turned_away(**over), ("bad_text", "too_long"))
        self.assertEqual(await self.rows(), [])

    async def test_a_speaker_must_be_a_confirmed_character_of_this_campaign(self) -> None:
        got = await self.add(speaker=self.aldric)
        self.assertEqual(got["upload"]["state"], "waiting")
        await self.sql("UPDATE lore_uploads SET state = 'done'")
        self.assertEqual(await self.turned_away(speaker="0" * 32), "bad_speaker")
        other = await self.name("Someone Else", self.two.id)
        self.assertEqual(await self.turned_away(speaker=other), "bad_speaker")

    async def test_only_one_upload_is_open_and_five_an_hour(self) -> None:
        first = await self.add()
        self.assertEqual(await self.turned_away(), "busy")
        for _ in range(4):
            await self.sql("UPDATE lore_uploads SET state = 'done'")
            await self.add()
        await self.sql("UPDATE lore_uploads SET state = 'done'")
        self.assertEqual(await self.turned_away(), "too_soon")
        self.assertEqual(len(await self.rows()), 5)
        self.assertEqual(first["upload"]["state"], "waiting")

    async def test_an_upload_nobody_took_ends_as_not_taken_and_blanks_its_words(self) -> None:
        await self.add()
        await self.sql("UPDATE lore_uploads SET created_at = created_at - 2000")
        await self.add()  # (the stale one is closed, so a new one is allowed)
        old, new = await self.rows()
        self.assertEqual((old["state"], old["text"], new["state"]), ("failed", "", "waiting"))

    async def test_only_the_campaigns_dm_can_add_or_read(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        self.assertEqual(await self.turned_away(who=bob), "not_allowed")
        got = await self.add()
        with self.assertRaises(RecapError) as err:
            await lore.read(self.web, bob, self.cref, got["upload"]["id"])
        self.assertEqual(err.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as err:
            await lore.read(self.web, bob, self.cref, None)
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(len(await self.rows()), 1)

    async def test_another_campaigns_upload_is_not_found(self) -> None:
        got = await self.add()
        other = f"{GUILD}.{self.two.id}"
        with self.assertRaises(RecapError):
            await lore.read(self.web, self.session, other, got["upload"]["id"])

    async def test_reading_with_none_open_is_null_and_a_bad_id_is_not_found(self) -> None:
        self.assertEqual(await lore.read(self.web, self.session, self.cref, None), {"upload": None})
        with self.assertRaises(RecapError) as err:
            await lore.read(self.web, self.session, self.cref, "ZZ")
        self.assertEqual(err.exception.code, "not_found")

    def test_the_limits_match_discords_upload_form(self) -> None:
        from dmbot.ui import name_lists

        self.assertEqual(
            (lore.PASTE_MAX, lore.LINK_MAX), (name_lists.PASTE_MAX, name_lists.LINK_MAX)
        )


class TheRoutes(TheDoors):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(lore_on=True), self.web, FakeDiscord(),
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
        url = f"/me/campaigns/{self.cref}/lore"
        got = await c.post(url, json=self.body())
        self.assertEqual(got.status_code, 202)
        uid = got.json()["upload"]["id"]
        self.assertEqual((await c.get(f"{url}/{uid}")).json()["upload"]["state"], "waiting")
        self.assertEqual((await c.get(url)).json()["upload"]["id"], uid)
        busy = await c.post(url, json=self.body())
        self.assertEqual((busy.status_code, busy.json()), (409, {"error": "busy"}))

    async def test_refusals_over_http(self) -> None:
        c = await self.client()
        url = f"/me/campaigns/{self.cref}/lore"
        got = await c.post(url, json=self.body(rights=False))
        self.assertEqual((got.status_code, got.json()), (409, {"error": "needs_confirm"}))
        got = await c.post(url, json=self.body(choice="x"))
        self.assertEqual((got.status_code, got.json()), (422, {"error": "bad_text"}))
        got = await c.post(url, json=["not", "an", "object"])
        self.assertEqual(got.status_code, 422)
        bob = await self.client(BOB)
        got = await bob.post(url, json=self.body())
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))

    async def test_off_by_default_nothing_is_queued(self) -> None:
        off = create_app(
            settings(), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        c = await self.client(app=off)
        url = f"/me/campaigns/{self.cref}/lore"
        self.assertEqual((await c.post(url, json=self.body())).status_code, 404)
        self.assertEqual((await c.get(url)).status_code, 404)
        self.assertEqual(await self.rows(), [])
