"""The website's "Say hello" forms (#665): the route against a fake GitHub and a real
database, and the GitHub and Turnstile clients against pretend servers over real HTTP."""

from __future__ import annotations

import datetime
import unittest
from typing import Any, ClassVar

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.config import ConfigError
from dmbot.web.app import create_app
from dmbot.web.feedback import (
    FeedbackError,
    GitHubDiscussions,
    Kind,
    RateLimit,
    Turnstile,
    discussion_body,
)
from dmbot.web.settings import load_web_settings
from tests.pg import DatabaseTest
from tests.test_web_api import API, SITE, FakeDiscord, settings
from tests.test_web_api import Settings as SettingsTest

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}
NOW = 1_800_000_000  # 2027-01-15 in UTC


class FakeDiscussions:
    def __init__(self) -> None:
        self.posts: list[tuple[Kind, str, str]] = []
        self.fail = False

    async def create(self, kind: Kind, title: str, body: str) -> int:
        if self.fail:
            raise FeedbackError("GitHub answered 502")
        self.posts.append((kind, title, body))
        return 100 + len(self.posts)


class FakeHumanCheck:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def verify(self, token: str, address: str) -> bool:
        self.seen.append((token, address))
        return token == "person"


class FeedbackRoute(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.github = FakeDiscussions()
        self.human = FakeHumanCheck()
        self.time = 0.0
        self.make_client()

    def make_client(self, **changes: Any) -> None:
        options: dict[str, Any] = {
            "discussions": self.github,
            "human_check": self.human,
            "feedback_limit": RateLimit(clock=lambda: self.time),
        }
        options.update(changes)
        app = create_app(
            settings(client_ip_header="CF-Connecting-IP"),
            self.db,
            FakeDiscord(),
            clock=lambda: NOW,
            **options,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def send(self, ip: str = "203.0.113.5", **fields: Any) -> httpx.Response:
        body = {"kind": "feedback", "message": "Loved it!", "turnstile": "person", **fields}
        return await self.client.post(
            "/feedback", json=body, headers={**HEADERS, "CF-Connecting-IP": ip}
        )

    async def stored(self) -> list[dict[str, Any]]:
        # DMbot can't read the table (it's add-only, #665); the test's own schema lifts
        # FORCE so its owner can look.
        async with self.db.unscoped() as conn:
            await conn.execute("ALTER TABLE feedback NO FORCE ROW LEVEL SECURITY")
            cur = await conn.execute(
                "SELECT kind, message, contact, discussion, created_at FROM feedback ORDER BY id"
            )
            return list(await cur.fetchall())

    async def test_feedback_becomes_a_discussion_with_only_the_message_and_date(self) -> None:
        response = await self.send(message="  The rules alerts are great.  ", contact="bel#1")
        self.assertEqual(response.status_code, 204)
        [(kind, title, body)] = self.github.posts
        self.assertEqual((kind, title), ("feedback", "Feedback from the website"))
        self.assertEqual(
            body,
            "Sent from the website on 2027-01-15.\n\n```text\nThe rules alerts are great.\n```\n",
        )
        self.assertNotIn("bel#1", body)
        self.assertNotIn("203.0.113.5", body)
        self.assertEqual(
            await self.stored(),
            [
                {
                    "kind": "feedback",
                    "message": "The rules alerts are great.",
                    "contact": "bel#1",
                    "discussion": 101,
                    "created_at": NOW,
                }
            ],
        )

    async def test_a_question_goes_to_the_questions_category(self) -> None:
        self.assertEqual(
            (await self.send(kind="question", message="Does it work?")).status_code, 204
        )
        self.assertEqual(self.github.posts[0][:2], ("question", "A question from the website"))
        self.assertIsNone((await self.stored())[0]["contact"])

    async def test_the_bot_and_website_can_add_but_not_read(self) -> None:
        await self.send()
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM feedback")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_one_post_per_address_per_ten_minutes(self) -> None:
        self.assertEqual((await self.send()).status_code, 204)
        again = await self.send()
        self.assertEqual((again.status_code, again.json()), (429, {"error": "slow_down"}))
        # Someone else can still post.
        self.assertEqual((await self.send(ip="198.51.100.7")).status_code, 204)
        self.time += 10 * 60
        self.assertEqual((await self.send()).status_code, 204)
        self.assertEqual(len(self.github.posts), 3)

    async def test_the_message_is_capped_at_2000_characters(self) -> None:
        self.assertEqual((await self.send(message="a" * 2000)).status_code, 204)
        long = await self.send(ip="198.51.100.7", message="a" * 2001)
        self.assertEqual((long.status_code, long.json()), (400, {"error": "too_long"}))
        self.assertEqual(len(self.github.posts), 1)

    async def test_bad_forms_are_refused_without_using_the_turn(self) -> None:
        for fields, error in (
            ({"message": "   "}, "empty"),
            ({"kind": "rant"}, "bad_request"),
            ({"message": 5}, "bad_request"),
            ({"contact": "x" * 201}, "contact_too_long"),
            ({"turnstile": "robot"}, "not_human"),
        ):
            response = await self.send(**fields)
            self.assertEqual((response.status_code, response.json()), (400, {"error": error}))
        self.assertEqual(self.github.posts, [])
        self.assertEqual((await self.send()).status_code, 204)
        self.assertEqual(self.human.seen[-1], ("person", "203.0.113.5"))

    async def test_control_and_reordering_characters_are_removed(self) -> None:
        sent = await self.send(message="Hi\x00 there\r\nbye\u202e!", contact="bel\n#1\x07")
        self.assertEqual(sent.status_code, 204)
        self.assertIn("```text\nHi there\nbye!\n```", self.github.posts[0][2])
        [row] = await self.stored()
        self.assertEqual((row["message"], row["contact"]), ("Hi there\nbye!", "bel #1"))

    async def test_storing_failing_after_the_post_still_says_sent_and_uses_the_turn(
        self,
    ) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute("DROP TABLE feedback")
        with self.assertLogs("dmbot.web.feedback", "ERROR") as logs:
            self.assertEqual((await self.send(message="secret words")).status_code, 204)
        self.assertNotIn("secret words", "\n".join(logs.output))
        self.assertIn("discussion 101", "\n".join(logs.output))
        # Sending again would only post it twice.
        self.assertEqual((await self.send()).status_code, 429)
        self.assertEqual(len(self.github.posts), 1)

    async def test_the_address_comes_only_from_a_real_address_in_the_header(self) -> None:
        # The test client's own connection address.
        self.assertEqual((await self.send(ip="")).status_code, 204)
        self.assertEqual((await self.send(ip="not-an-address")).status_code, 429)
        self.assertEqual(self.human.seen[-1][1], "127.0.0.1")
        # One IPv6 network is one person, whatever address it picks.
        self.assertEqual((await self.send(ip="2001:db8:1:2::1")).status_code, 204)
        self.assertEqual((await self.send(ip="2001:db8:1:2::ffff")).status_code, 429)
        self.assertEqual((await self.send(ip="2001:db8:1:3::1")).status_code, 204)

    async def test_without_the_header_setting_the_header_is_ignored(self) -> None:
        await self.client.aclose()
        app = create_app(
            settings(),
            self.db,
            FakeDiscord(),
            discussions=self.github,
            feedback_limit=RateLimit(clock=lambda: self.time),
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API)
        self.assertEqual((await self.send(ip="203.0.113.5")).status_code, 204)
        self.assertEqual((await self.send(ip="198.51.100.7")).status_code, 429)

    async def test_deeply_nested_json_is_a_bad_request(self) -> None:
        response = await self.client.post(
            "/feedback",
            content=b"[" * 20_000,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        self.assertEqual((response.status_code, response.json()), (400, {"error": "bad_request"}))

    async def test_a_huge_body_is_refused(self) -> None:
        response = await self.client.post(
            "/feedback", content=b"x" * 40_000, headers={**HEADERS, "Content-Type": "text/plain"}
        )
        self.assertEqual(response.status_code, 413)

    async def test_github_failing_stores_nothing_and_lets_them_try_again(self) -> None:
        self.github.fail = True
        failed = await self.send()
        self.assertEqual(
            (failed.status_code, failed.json()), (502, {"error": "feedback_unavailable"})
        )
        self.assertEqual(await self.stored(), [])
        self.github.fail = False
        self.assertEqual((await self.send()).status_code, 204)

    async def test_without_a_github_token_the_forms_are_off(self) -> None:
        await self.client.aclose()
        self.make_client(discussions=None)
        response = await self.send()
        self.assertEqual((response.status_code, response.json()), (503, {"error": "feedback_off"}))

    async def test_without_turnstile_the_form_still_works(self) -> None:
        await self.client.aclose()
        self.make_client(human_check=None)
        self.assertEqual((await self.send(turnstile=None)).status_code, 204)

    async def test_it_needs_the_websites_header(self) -> None:
        response = await self.client.post("/feedback", json={"kind": "feedback", "message": "hi"})
        self.assertEqual(response.status_code, 403)


class Body(unittest.TestCase):
    def test_markdown_and_mentions_stay_plain_text(self) -> None:
        body = discussion_body(
            "@everyone ```` see #1 ![x](https://e.example)", datetime.date(2027, 1, 15)
        )
        self.assertTrue(
            body.endswith("`````text\n@everyone ```` see #1 ![x](https://e.example)\n`````\n")
        )

    def test_a_message_cant_close_its_block(self) -> None:
        message = "````\n~~~\n```"
        body = discussion_body(message, datetime.date(2027, 1, 15))
        # The fence is longer than any run of backticks in the message, so no line of the
        # message can end the block early.
        self.assertTrue(body.endswith("`````text\n````\n~~~\n```\n`````\n"))


class PretendGitHub:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.categories = [{"id": "C_fb", "name": "Feedback"}, {"id": "C_q", "name": "Questions"}]
        self.errors = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/graphql", self.graphql)
        return app

    async def graphql(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer gh-token":
            return web.json_response({"message": "Bad credentials"}, status=401)
        payload = await request.json()
        self.requests.append(payload)
        if self.errors:
            return web.json_response({"errors": [{"message": "SECRET_ECHO"}]})
        if payload["query"].startswith("query"):
            return web.json_response(
                {
                    "data": {
                        "repository": {
                            "id": "R_1",
                            "discussionCategories": {"nodes": self.categories},
                        }
                    }
                }
            )
        return web.json_response({"data": {"createDiscussion": {"discussion": {"number": 7}}}})


class GitHubClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pretend = PretendGitHub()
        self.server = TestServer(self.pretend.app())
        await self.server.start_server()
        self.github = GitHubDiscussions(
            "gh-token", "smv620/Discord-DMbot", url=str(self.server.make_url("/graphql"))
        )

    async def asyncTearDown(self) -> None:
        await self.github.close()
        await self.server.close()

    async def test_it_posts_in_the_right_category(self) -> None:
        self.assertEqual(await self.github.create("question", "T", "B"), 7)
        self.assertEqual(await self.github.create("feedback", "T2", "B2"), 7)
        lookup, first, second = self.pretend.requests  # the ids are looked up once
        self.assertEqual(lookup["variables"], {"owner": "smv620", "name": "Discord-DMbot"})
        self.assertEqual(
            first["variables"], {"repo": "R_1", "category": "C_q", "title": "T", "body": "B"}
        )
        self.assertIn("createDiscussion", first["query"])
        self.assertEqual(second["variables"]["category"], "C_fb")

    async def test_a_missing_category_is_an_error_and_is_looked_up_again(self) -> None:
        self.pretend.categories = []
        with self.assertRaisesRegex(FeedbackError, "Questions"):
            await self.github.create("question", "T", "B")
        self.pretend.categories = [{"id": "C_q", "name": "Questions"}]
        self.assertEqual(await self.github.create("question", "T", "B"), 7)

    async def test_graphql_errors_are_feedback_errors_without_their_text(self) -> None:
        self.pretend.errors = True
        with self.assertRaises(FeedbackError) as caught:
            await self.github.create("feedback", "T", "B")
        self.assertNotIn("SECRET_ECHO", str(caught.exception))

    async def test_an_unexpected_answer_is_a_feedback_error(self) -> None:
        self.pretend.categories = [{"label": "Feedback"}]  # no name or id
        with self.assertRaises(FeedbackError):
            await self.github.create("feedback", "T", "B")


class TurnstileClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.forms: list[dict[str, str]] = []

        async def verify(request: web.Request) -> web.Response:
            form = {k: str(v) for k, v in (await request.post()).items()}
            self.forms.append(form)
            host = "elsewhere.example" if form["response"] == "other-site" else "dmbot.example"
            return web.json_response(
                {"success": form["response"] in ("ok", "other-site"), "hostname": host}
            )

        app = web.Application()
        app.router.add_post("/verify", verify)
        self.server = TestServer(app)
        await self.server.start_server()
        self.turnstile = Turnstile(
            "ts-secret", "dmbot.example", url=str(self.server.make_url("/verify"))
        )

    async def asyncTearDown(self) -> None:
        await self.turnstile.close()
        await self.server.close()

    async def test_it_asks_cloudflare(self) -> None:
        self.assertTrue(await self.turnstile.verify("ok", "203.0.113.5"))
        self.assertFalse(await self.turnstile.verify("bad", "203.0.113.5"))
        # Solved, but on another website.
        self.assertFalse(await self.turnstile.verify("other-site", "203.0.113.5"))
        self.assertFalse(await self.turnstile.verify("", "203.0.113.5"))  # not even asked
        self.assertEqual(
            self.forms[0], {"secret": "ts-secret", "response": "ok", "remoteip": "203.0.113.5"}
        )
        self.assertEqual(len(self.forms), 3)


class FeedbackSettings(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        **SettingsTest.ENV,
        "GITHUB_FEEDBACK_TOKEN": "gh-tok-1",
        "TURNSTILE_SECRET_KEY": "ts-key-1",
        "WEB_CLIENT_IP_HEADER": "CF-Connecting-IP",
    }

    def test_reads_the_feedback_settings_and_hides_secrets(self) -> None:
        loaded = load_web_settings(self.ENV)
        self.assertEqual(loaded.feedback_repo, "smv620/Discord-DMbot")
        self.assertEqual(loaded.client_ip_header, "CF-Connecting-IP")
        self.assertNotIn("gh-tok-1", repr(loaded))
        self.assertNotIn("ts-key-1", repr(loaded))

    def test_turnstile_is_needed_except_on_localhost(self) -> None:
        env = {**self.ENV, "TURNSTILE_SECRET_KEY": ""}
        with self.assertRaisesRegex(ConfigError, "TURNSTILE_SECRET_KEY"):
            load_web_settings(env)
        local = load_web_settings({**env, "WEB_API_URL": "http://localhost:8080"})
        self.assertEqual(local.turnstile_secret, "")

    def test_the_address_header_is_needed_except_on_localhost(self) -> None:
        env = {**self.ENV, "WEB_CLIENT_IP_HEADER": ""}
        with self.assertRaisesRegex(ConfigError, "WEB_CLIENT_IP_HEADER"):
            load_web_settings(env)
        load_web_settings({**env, "WEB_API_URL": "http://localhost:8080"})

    def test_bad_values_are_refused(self) -> None:
        for name, value in (
            ("GITHUB_FEEDBACK_REPO", "not a repo"),
            ("WEB_CLIENT_IP_HEADER", "X: evil"),
        ):
            with self.assertRaises(ConfigError):
                load_web_settings({**self.ENV, name: value})


if __name__ == "__main__":
    unittest.main()
