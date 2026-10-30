"""Recap style on the website (#2985): the DM-only doors, the shapes and limits the door checks,
isolation, nothing of the words in what comes back about a queued change, and the stale-change
sweep. Invented names and ids."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.recaps.style import EXAMPLE_MAX_WORDS, MAX_CHARS, NOTE_CHARS
from dmbot.web import recap_style, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_report import GUILD, Reading

WORDS = "The Silver Lock clicked and Mira let out her breath."


class TheDoors(Reading):
    async def put(self, action: str, arg: str = "", who: Any = None) -> dict[str, Any]:
        body = {"action": action, "arg": arg}
        return await recap_style.add(self.web, who or self.session, self.cref, body)

    async def turned_away(self, action: str, arg: str = "", who: Any = None) -> str:
        with self.assertRaises(RecapError) as err:
            await self.put(action, arg, who)
        return err.exception.code

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT action, arg, state, error, user_id FROM web_recap_style_actions"
                " ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def test_the_limits_match_discords_panel(self) -> None:
        import importlib

        migration = importlib.import_module("dmbot.migrations.0290_web_recap_style")
        self.assertEqual(migration.EXAMPLE_WORDS, EXAMPLE_MAX_WORDS)
        self.assertEqual(migration.EXAMPLE_CHARS, MAX_CHARS)
        self.assertEqual(migration.NOTE_CHARS, NOTE_CHARS)
        self.assertEqual(
            (recap_style.EXAMPLE_CHARS, recap_style.NOTE_CHARS), (MAX_CHARS, NOTE_CHARS)
        )

    async def test_a_change_is_queued_for_the_bot_and_polled_without_its_words(self) -> None:
        got = await self.put("add_example", WORDS)
        [row] = await self.rows()
        self.assertEqual(
            (row["action"], row["arg"], row["state"]), ("add_example", WORDS, "waiting")
        )
        self.assertEqual(row["user_id"], ALICE.id)
        seen = await recap_style.action(self.web, self.session, self.cref, got["id"])
        self.assertEqual(
            (seen["state"], seen["action"], seen["error"]), ("waiting", "add_example", "")
        )
        self.assertNotIn("Silver", repr(seen))

    async def test_each_action_has_its_shape(self) -> None:
        self.assertEqual((await self.put("use_defaults"))["id"] != "", True)
        self.assertEqual((await self.put("remove_example", "12"))["id"] != "", True)
        self.assertEqual((await self.put("remove_note", "3"))["id"] != "", True)
        self.assertEqual((await self.put("add_note", "Keep it short."))["id"] != "", True)
        for action, arg in (
            ("use_defaults", "x"),
            ("remove_example", "twelve"),
            ("remove_example", ""),
            ("remove_note", "-1"),
            ("add_example", "   "),
            ("add_note", ""),
            ("add_note", "x" * (NOTE_CHARS + 1)),
            ("add_example", "word " * (EXAMPLE_MAX_WORDS + 1)),
        ):
            with self.subTest(action=action, arg=arg[:8]):
                self.assertEqual(await self.turned_away(action, arg), "bad_arg")
        self.assertEqual(await self.turned_away("explode"), "bad_action")
        self.assertEqual(await self.turned_away("add_example", "x" * (MAX_CHARS + 1)), "too_long")
        self.assertEqual(await self.turned_away("add_example", "a\x00b"), "bad_arg")
        self.assertEqual(await self.turned_away("add_example", "a\ud800b"), "bad_arg")
        self.assertEqual(len(await self.rows()), 4)

    async def test_the_full_word_limit_is_taken(self) -> None:
        got = await self.put("add_example", " ".join(["word"] * EXAMPLE_MAX_WORDS))
        self.assertEqual(len(got["id"]), 32)

    async def test_ten_open_at_once_is_the_most(self) -> None:
        for _ in range(10):
            await self.put("use_defaults")
        self.assertEqual(await self.turned_away("use_defaults"), "too_soon")

    async def test_a_change_nobody_took_ends_as_failed_and_loses_its_words_on_read(self) -> None:
        got = await self.put("add_example", WORDS)
        await self.sql("UPDATE web_recap_style_actions SET updated_at = updated_at - 2000")
        seen = await recap_style.action(self.web, self.session, self.cref, got["id"])
        self.assertEqual((seen["state"], seen["error"]), ("failed", "not_taken"))
        [row] = await self.rows()
        self.assertEqual(row["arg"], "")

    async def test_the_read_door_gives_the_dm_their_examples_notes_and_the_defaults_flag(
        self,
    ) -> None:
        empty = await recap_style.read(self.web, self.session, self.cref)
        self.assertEqual(empty, {"examples": [], "notes": [], "defaults": True})
        await self.sql(
            "INSERT INTO recap_style_examples (guild_id, campaign_id, position, text, created_by,"
            " created_at) VALUES (%s, %s, 1, %s, %s, 1)",
            GUILD, self.one.id, WORDS, ALICE.id,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
            " created_at) VALUES (%s, %s, 1, %s, %s, 1)",
            GUILD, self.one.id, "Keep it short.", ALICE.id,
        )  # fmt: skip
        got = await recap_style.read(self.web, self.session, self.cref)
        self.assertEqual(got["defaults"], False)
        self.assertEqual([e["text"] for e in got["examples"]], [WORDS])
        self.assertEqual([n["text"] for n in got["notes"]], ["Keep it short."])

    async def test_only_the_campaigns_dm_can_read_change_or_poll(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        got = await self.put("use_defaults")
        self.assertEqual(await self.turned_away("use_defaults", who=bob), "not_allowed")
        with self.assertRaises(RecapError) as err:
            await recap_style.read(self.web, bob, self.cref)
        self.assertEqual(err.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as err:
            await recap_style.action(self.web, bob, self.cref, got["id"])
        self.assertEqual(err.exception.code, "not_allowed")
        other = f"{GUILD}.{self.two.id}"
        with self.assertRaises(RecapError):
            await recap_style.action(self.web, self.session, other, got["id"])
        self.assertEqual(len(await self.rows()), 1)

    async def test_a_bad_id_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as err:
            await recap_style.action(self.web, self.session, self.cref, "nope")
        self.assertEqual(err.exception.code, "not_found")


class TheRoutes(TheDoors):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(recap_style_on=True), self.web, FakeDiscord(),
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

    async def test_read_change_and_poll_over_http(self) -> None:
        c = await self.client()
        url = f"/me/campaigns/{self.cref}/recap-style"
        self.assertEqual((await c.get(url)).json()["defaults"], True)
        got = await c.post(url, json={"action": "add_note", "arg": "Keep it short."})
        self.assertEqual(got.status_code, 202)
        seen = await c.get(f"{url}/{got.json()['id']}")
        self.assertEqual((seen.status_code, seen.json()["state"]), (200, "waiting"))

    async def test_refusals_over_http(self) -> None:
        c = await self.client()
        url = f"/me/campaigns/{self.cref}/recap-style"
        for body, status, word in (
            ({"action": "explode"}, 400, "bad_action"),
            ({"action": "add_note", "arg": ""}, 422, "bad_arg"),
            ({"action": "add_example", "arg": "x" * (MAX_CHARS + 1)}, 422, "too_long"),
        ):
            got = await c.post(url, json=body)
            self.assertEqual((got.status_code, got.json()), (status, {"error": word}))
        self.assertEqual((await c.post(url, json=["not", "an", "object"])).status_code, 422)
        bob = await self.client(BOB)
        got = await bob.post(url, json={"action": "use_defaults"})
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))
        self.assertEqual((await bob.get(url)).status_code, 403)

    async def test_off_by_default_nothing_is_queued(self) -> None:
        off = create_app(
            settings(), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        c = await self.client(app=off)
        url = f"/me/campaigns/{self.cref}/recap-style"
        self.assertEqual((await c.get(url)).status_code, 404)
        self.assertEqual((await c.post(url, json={"action": "use_defaults"})).status_code, 404)
        self.assertEqual(await self.rows(), [])
