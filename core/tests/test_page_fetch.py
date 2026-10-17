"""Fetching one page for homebrew (#1634): the guards of dmbot.fetch (nothing but the public
internet) plus this feature's: redirects stay on the allowed site, no cookies, a plain user
agent, 2 MB, a few seconds, one at a time per campaign and a few an hour."""

from __future__ import annotations

import asyncio
import gzip
import inspect
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.fetch import STRICT, LinkError, Policy
from dmbot.rules import page_fetch
from dmbot.rules.page_fetch import FetchGate, PageError, Reply, fetch_page


class Served(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.routes: dict[str, Callable[[web.Request], Any]] = {}
        self.seen: list[web.Request] = []

        async def handler(request: web.Request) -> Any:
            self.seen.append(request)
            result = self.routes[request.path](request)
            return await result if inspect.isawaitable(result) else result

        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", handler)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        assert self.server.port is not None
        self.base = f"http://127.0.0.1:{self.server.port}"
        self.policy = Policy(
            schemes=frozenset({"http"}),
            ports=frozenset({self.server.port}),
            allow_ip=lambda ip: str(ip) == "127.0.0.1",
        )

    async def asyncTearDown(self) -> None:
        await self.server.close()

    def redirect(self, path: str, to: str) -> None:
        self.routes[path] = lambda _request: web.Response(status=302, headers={"Location": to})

    async def get(self, path: str, ok: bool = True) -> Reply:
        return await fetch_page(self.base + path, redirect_ok=lambda host: ok, policy=self.policy)


class Fetching(Served):
    async def test_the_status_and_text_come_back_with_a_plain_user_agent_and_no_cookies(
        self,
    ) -> None:
        self.routes["/a"] = lambda r: web.Response(
            text="<p>Hello</p>", headers={"Set-Cookie": "a=1"}
        )
        self.routes["/b"] = lambda r: web.Response(text="second")
        first = await self.get("/a")
        self.assertEqual((first.status, first.text), (200, "<p>Hello</p>"))
        await self.get("/b")
        self.assertIn("DMbot", self.seen[0].headers["User-Agent"])
        self.assertNotIn("Cookie", self.seen[1].headers)  # (each fetch has its own session)

    async def test_a_404_is_an_answer_not_an_error(self) -> None:
        self.routes["/gone"] = lambda r: web.Response(status=404, text="nope")
        got = await self.get("/gone")
        self.assertEqual((got.status, got.text), (404, "nope"))

    async def test_a_redirect_is_followed_only_where_the_caller_allows(self) -> None:
        self.routes["/start"] = lambda r: web.Response(status=302, headers={"Location": "/end"})
        self.routes["/end"] = lambda r: web.Response(text="there")
        self.assertEqual((await self.get("/start")).text, "there")
        with self.assertRaises(PageError):
            await self.get("/start", ok=False)  # off the site: refused, not followed
        self.assertEqual(len([r for r in self.seen if r.path == "/end"]), 1)

    async def test_a_redirect_to_another_port_scheme_or_login_is_refused(self) -> None:
        for n, target in enumerate(
            (
                "http://127.0.0.1:1/x",  # another port
                "https://127.0.0.1/x",  # another scheme (the test policy allows only http)
                "http://user:pw@127.0.0.1:{port}/x",  # a sign-in in the address
            )
        ):
            location = target.format(port=self.server.port)
            self.redirect(f"/r{n}", location)
            with self.assertRaises(PageError):
                await self.get(f"/r{n}")

    async def test_a_redirect_loop_and_a_redirect_with_nowhere_to_go_are_refused(self) -> None:
        self.routes["/loop"] = lambda r: web.Response(status=302, headers={"Location": "/loop"})
        self.routes["/none"] = lambda r: web.Response(status=302)
        for path in ("/loop", "/none"):
            with self.assertRaises(PageError):
                await self.get(path)

    async def test_a_gzipped_page_is_unpacked_and_a_page_over_the_limit_is_refused(self) -> None:
        self.routes["/z"] = lambda r: web.Response(
            body=gzip.compress(b"<p>zipped</p>"), headers={"Content-Encoding": "gzip"}
        )
        self.assertEqual((await self.get("/z")).text, "<p>zipped</p>")
        self.routes["/big"] = lambda r: web.Response(body=b"x" * 1000)
        self.routes["/bomb"] = lambda r: web.Response(
            body=gzip.compress(b"x" * 5000), headers={"Content-Encoding": "gzip"}
        )
        with patch.object(page_fetch, "MAX_BYTES", 500):
            for path in ("/big", "/bomb"):
                with self.assertRaises(PageError):
                    await self.get(path)

    async def test_a_page_that_never_finishes_times_out(self) -> None:
        async def slow(request: web.Request) -> web.Response:
            await asyncio.sleep(5)
            return web.Response(text="late")

        self.routes["/slow"] = lambda r: slow(r)
        with patch.object(page_fetch, "TIMEOUT_S", 0.2), self.assertRaises(PageError):
            await self.get("/slow")

    async def test_nothing_but_the_public_internet_can_be_reached(self) -> None:
        self.routes["/a"] = lambda r: web.Response(text="inside")
        with self.assertRaises(PageError):  # the real policy refuses 127.0.0.1 and http
            await fetch_page(self.base + "/a", redirect_ok=lambda host: True)
        for url in ("https://127.0.0.1/a", "https://169.254.169.254/a", "https://[::1]/a"):
            with self.assertRaises(PageError):
                await fetch_page(url, redirect_ok=lambda host: True)


class Hosts(unittest.TestCase):
    def test_a_redirect_stays_on_the_allowed_site(self) -> None:
        from dmbot.rules.homebrew_import import _redirect_ok

        for good in ("dndbeyond.com", "www.dndbeyond.com", "WWW.DNDBeyond.com", "dndbeyond.com."):
            self.assertTrue(_redirect_ok(good.casefold()), good)
        for bad in (
            "dndbeyond.com.evil.example",
            "evil-dndbeyond.com",
            "notdndbeyond.com",
            "media.dndbeyond.com",
            "127.0.0.1",
            "0x7f.0.0.1",
            "",
        ):
            self.assertFalse(_redirect_ok(bad), bad)


class Resolving(unittest.IsolatedAsyncioTestCase):
    async def test_a_name_that_resolves_to_a_private_address_is_refused(self) -> None:
        from unittest.mock import AsyncMock

        from dmbot.fetch import _PublicResolver

        found = [
            {"hostname": "x", "host": "10.0.0.5", "port": 443, "family": 2, "proto": 6, "flags": 0}
        ]
        mixed = [*found, {**found[0], "host": "8.8.8.8"}]  # ANY private address refuses the host
        for answer in (found, mixed):
            with patch.object(_PublicResolver, "__init__", lambda self, policy: None):
                resolver = _PublicResolver(None)  # type: ignore[arg-type]
                resolver._inner = AsyncMock()
                resolver._inner.resolve.return_value = answer
                resolver._policy = STRICT
                with self.assertRaises(LinkError):
                    await resolver.resolve("example.test", 443)


class Gate(unittest.TestCase):
    def test_one_at_a_time_per_campaign_and_a_few_in_a_window(self) -> None:
        now = [0.0]
        gate = FetchGate(clock=lambda: now[0])
        gate.enter(1, "a")
        with self.assertRaises(PageError) as busy:
            gate.enter(1, "a")
        self.assertEqual(str(busy.exception), page_fetch.BUSY)
        gate.enter(1, "b")  # another campaign is not held
        gate.enter(2, "a")  # nor the same campaign id in another server
        gate.leave(1, "a")
        for _ in range(page_fetch.PER_WINDOW - 1):
            gate.enter(1, "a")
            gate.leave(1, "a")
        with self.assertRaises(PageError) as often:
            gate.enter(1, "a")
        self.assertEqual(str(often.exception), page_fetch.TOO_OFTEN)
        now[0] = page_fetch.WINDOW_S + 1  # the window moves on
        gate.enter(1, "a")


if __name__ == "__main__":
    unittest.main()
