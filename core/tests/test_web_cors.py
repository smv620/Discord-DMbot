"""The browser's preflight is answered for every method the API routes (#2415).

The website and the API are on different origins, so a browser asks first (OPTIONS) before a PUT
or DELETE. When the list of allowed methods missed them, every save from the site failed as
"couldn't reach DMbot" while the tests, which call the app directly, stayed green.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import httpx

from dmbot.web.app import create_app
from tests.test_web_api import SITE, FakeDiscord, settings


class Preflight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # (a preflight is answered by the CORS layer before any route or database is touched)
        self.app = create_app(settings(), MagicMock(), FakeDiscord())
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def ask(self, path: str, method: str, origin: str = SITE) -> httpx.Response:
        return await self.client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )

    def routed(self) -> list[tuple[str, str]]:
        found = {
            (route.path, method)  # type: ignore[attr-defined]  # (only routes with methods)
            for route in self.app.routes
            for method in getattr(route, "methods", None) or ()
            if method not in ("HEAD", "OPTIONS")
        }
        return sorted(found)

    async def test_every_routed_method_is_allowed_from_the_site(self) -> None:
        routed = self.routed()
        self.assertIn("PUT", {m for _, m in routed})  # the walk really sees the PUT routes
        self.assertIn("DELETE", {m for _, m in routed})
        for path, method in routed:
            with self.subTest(path=path, method=method):
                got = await self.ask(path, method)
                self.assertEqual(got.status_code, 200, got.text)
                allowed = got.headers["access-control-allow-methods"].split(", ")
                self.assertIn(method, allowed)
                self.assertEqual(got.headers["access-control-allow-origin"], SITE)
                self.assertEqual(got.headers["access-control-allow-credentials"], "true")

    async def test_another_origin_is_refused(self) -> None:
        for method in ("GET", "PUT", "DELETE"):
            got = await self.ask("/me/recaps/x", method, "https://elsewhere.example")
            self.assertEqual(got.status_code, 400)
            self.assertNotIn("access-control-allow-origin", got.headers)

    async def test_a_method_no_route_uses_is_not_allowed(self) -> None:
        got = await self.ask("/me/recaps/x", "PATCH")
        self.assertEqual(got.status_code, 400)
