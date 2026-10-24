"""The public paths of the web API live in code (#2625): the middleware that gives the tunnel's
requests the empty 404 for anything else, a test that every route of the app is sorted (public,
or inside only with a reason), and a test that every link the code builds on WEB_API_URL lands
on a path that is either public or closed on purpose. No database is needed."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from starlette.routing import Route

from dmbot.web import public_paths as pp
from dmbot.web.app import create_app
from dmbot.web.settings import WebSettings

API = "https://api.dmbot.example"
INSIDE = "http://web-api:8080"  # how the Docker network and dev1 reach it
SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"


def make_app() -> Any:
    settings = WebSettings(
        database_url="unused",
        discord_client_id="123",
        discord_client_secret="shh",
        secret_key=b"k" * 40,
        site_url="https://dmbot.example",
        api_url=API,
    )
    return create_app(settings, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]


def client(app: Any, base: str = INSIDE) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


class ThePaths(unittest.TestCase):
    def test_each_public_path_passes_its_own_examples(self) -> None:
        for entry in pp.PUBLIC:
            self.assertTrue(entry.reason and entry.examples, entry.pattern.pattern)
            for example in entry.examples:
                self.assertTrue(pp.is_public(example), example)

    def test_what_stays_closed_is_not_public(self) -> None:
        for path in ("/health", "/", "/install", "/personal", "/plan/try-it", "/administrator"):
            self.assertFalse(pp.is_public(path), path)
        # a prefix is not a word: /me is the site's, /member is not
        self.assertFalse(pp.is_public("/member"))
        self.assertFalse(pp.is_public("/webhooks/other"))

    def test_the_tunnels_old_routes_are_all_still_public(self) -> None:
        for path in (
            "/admin",
            "/webhooks/stripe",
            "/auth/discord/callback",
            "/me",
            "/billing/checkout",
            "/mail/inbound",
            "/support",
            "/recap-audio/a/voice",
        ):
            self.assertTrue(pp.is_public(path), path)

    def test_every_reason_is_written_down(self) -> None:
        self.assertTrue(all(reason for _, reason in pp.INSIDE_ONLY))


class TheMiddleware(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = make_app()
        self.http = client(self.app)

    async def asyncTearDown(self) -> None:
        await self.http.aclose()

    async def test_a_private_path_through_the_tunnel_gets_the_empty_404(self) -> None:
        got = await self.http.get("/health", headers={pp.TUNNEL_HEADER: "203.0.113.5"})
        self.assertEqual((got.status_code, got.content), (404, b""))

    async def test_the_same_path_from_inside_works(self) -> None:
        got = await self.http.get("/health")
        self.assertEqual((got.status_code, got.json()), (200, {"status": "ok"}))

    async def test_a_public_path_through_the_tunnel_reaches_the_app(self) -> None:
        got = await self.http.get("/me", headers={pp.TUNNEL_HEADER: "203.0.113.5"})
        self.assertEqual((got.status_code, got.json()), (401, {"error": "signed_out"}))

    async def test_the_public_host_alone_is_outside_even_without_the_tunnels_header(self) -> None:
        outside = client(self.app, API)
        try:
            got = await outside.get("/health")
            self.assertEqual((got.status_code, got.content), (404, b""))
            got = await outside.get("/health", headers={"Host": "API.dmbot.example:443"})
            self.assertEqual(got.status_code, 404)  # (case and a port make no difference)
            got = await outside.get("/me")
            self.assertEqual(got.status_code, 401)  # a public path still reaches the app
        finally:
            await outside.aclose()

    async def test_the_inside_hosts_are_not_affected(self) -> None:
        for base in ("http://web-api:8080", "http://localhost:8080", "http://127.0.0.1:8080"):
            inside = client(self.app, base)
            try:
                got = await inside.get("/health")
                self.assertEqual((got.status_code, got.json()), (200, {"status": "ok"}), base)
            finally:
                await inside.aclose()

    async def test_a_path_that_is_not_a_route_is_also_empty_from_outside(self) -> None:
        got = await self.http.get("/nothing-here", headers={pp.TUNNEL_HEADER: "203.0.113.5"})
        self.assertEqual((got.status_code, got.content), (404, b""))

    async def test_a_private_post_is_the_empty_404_before_any_other_check(self) -> None:
        got = await self.http.post("/plan/try-it", headers={pp.TUNNEL_HEADER: "203.0.113.5"})
        self.assertEqual((got.status_code, got.content), (404, b""))


class EveryRouteIsSorted(unittest.TestCase):
    def sort(self, routes: list[str]) -> list[str]:
        """The routes nobody has decided about."""
        app = make_app()
        found = [r for r in app.routes if isinstance(r, Route)]
        unsorted = []
        for route in found:
            if route.path not in routes and not self.decided(route):
                unsorted.append(route.path)
        return unsorted

    @staticmethod
    def decided(route: Route) -> bool:
        examples = [e for p in pp.PUBLIC for e in p.examples]
        if any(route.path_regex.match(e) for e in examples):
            return True  # a public path reaches it
        sample = re.sub(r"\{[^}]*\}", "x", route.path)
        return pp.is_public(sample) or pp.inside_only_reason(sample) is not None

    def test_no_route_waits_for_a_decision(self) -> None:
        self.assertEqual(self.sort([]), [], "Put each on public_paths.PUBLIC or INSIDE_ONLY")

    def test_an_unsorted_route_is_caught(self) -> None:
        app = make_app()

        async def new() -> dict[str, str]:  # pragma: no cover - never called
            return {}

        app.add_api_route("/brand-new-route", new)

        found = [r for r in app.routes if isinstance(r, Route) and r.path == "/brand-new-route"]
        self.assertEqual(len(found), 1)
        self.assertFalse(self.decided(found[0]))

    def test_the_known_routes_include_the_ones_the_old_tunnel_missed(self) -> None:
        paths = {r.path for r in make_app().routes if isinstance(r, Route)}
        self.assertIn("/recap-audio/{ref}/{kind}", paths)
        self.assertIn("/health", paths)


class EveryLinkLandsOnAnOpenPath(unittest.TestCase):
    LINK = re.compile(r"\{[A-Za-z_.()]*api_url[^}]*\}(/[A-Za-z0-9_\-/{}.]+)")

    def links(self) -> dict[str, str]:
        """The path of every f-string link built on the API's address, with where it is."""
        found: dict[str, str] = {}
        for file in SRC.rglob("*.py"):
            for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                for match in self.LINK.finditer(line):
                    path = re.sub(r"\{[^}]*\}", "x", match.group(1))
                    found[path] = f"{file.relative_to(SRC)}:{line_no}"
        return found

    def test_the_scan_finds_the_links_it_should(self) -> None:
        paths = self.links()
        self.assertIn("/recap-audio/x/x", paths)
        self.assertIn("/recaps/x/audio/x", paths)

    def test_each_one_is_public_or_closed_on_purpose(self) -> None:
        for path, where in self.links().items():
            self.assertTrue(
                pp.is_public(path) or pp.inside_only_reason(path) is not None,
                f"{where} builds a link to {path}, which nobody has sorted",
            )

    def test_the_redirects_discord_calls_back_to_are_sorted(self) -> None:
        settings = WebSettings(
            database_url="unused",
            discord_client_id="1",
            discord_client_secret="s",
            secret_key=b"k" * 40,
            site_url="https://dmbot.example",
            api_url=API,
        )
        for uri in (settings.oauth_redirect_uri, settings.install_redirect_uri):
            path = uri.removeprefix(API)
            self.assertTrue(pp.is_public(path) or pp.inside_only_reason(path) is not None, path)
