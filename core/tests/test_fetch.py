"""Opening links safely (#264): share links become download links, and nothing but the
public internet can be reached, including through redirects and DNS."""

import asyncio
import gzip
import inspect
import ipaddress
import unittest
from typing import Any
from unittest.mock import patch

import yarl
from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot import fetch
from dmbot.fetch import LinkError, Policy, check_url, direct_url, is_public

DOC = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


class DirectLinks(unittest.TestCase):
    def test_file_hosts_become_download_links(self) -> None:
        cases = {
            f"https://docs.google.com/document/d/{DOC}/edit?usp=drivesdk":
                f"https://docs.google.com/document/d/{DOC}/export?format=txt",
            f"https://docs.google.com/document/u/1/d/{DOC}/edit":
                f"https://docs.google.com/document/d/{DOC}/export?format=txt",
            f"https://drive.google.com/file/d/{DOC}/view?usp=sharing":
                f"https://drive.google.com/uc?export=download&id={DOC}",
            "https://www.dropbox.com/scl/fi/abc/npcs.pdf?rlkey=x&dl=0":
                "https://www.dropbox.com/scl/fi/abc/npcs.pdf?rlkey=x&dl=1",
            "https://contoso.sharepoint.com/:w:/s/team/Eabc":
                "https://contoso.sharepoint.com/:w:/s/team/Eabc?download=1",
            "www.example.com/npcs.html": "https://www.example.com/npcs.html",
            f"docs.google.com/document/d/{DOC}/edit":
                f"https://docs.google.com/document/d/{DOC}/export?format=txt",
            "example.com/npcs.pdf": "https://example.com/npcs.pdf",
            " <https://example.com/lore> ": "https://example.com/lore",
        }  # fmt: skip
        for link, want in cases.items():
            with self.subTest(link):
                self.assertEqual(str(direct_url(link)), want)

    def test_onedrive_goes_through_its_sharing_api(self) -> None:
        url = direct_url("https://1drv.ms/w/s!AbCd")
        self.assertEqual(url.host, "api.onedrive.com")
        self.assertTrue(url.path.startswith("/v1.0/shares/u!"))
        self.assertTrue(url.path.endswith("/root/content"))

    def test_lookalike_hosts_are_not_converted(self) -> None:
        link = f"https://docs.google.com.evil.example/document/d/{DOC}/edit"
        self.assertEqual(str(direct_url(link)), link)

    def test_not_a_link(self) -> None:
        for text in ("Belleros", "", "https://", "just some words"):
            with self.subTest(text), self.assertRaises(LinkError):
                direct_url(text)


class Addresses(unittest.TestCase):
    def test_only_the_public_internet(self) -> None:
        refused = [
            "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
            "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
            "::ffff:127.0.0.1", "::ffff:169.254.169.254", "192.0.2.1",
            "64:ff9b::a00:1", "64:ff9b::a9fe:a9fe", "::ffff:0:a00:1", "::7f00:1",
            "2002:a00:1::1", "2002:a9fe:a9fe::1",  # 6to4 around 10.0.0.1, 169.254.169.254
            "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo, client 10.0.0.1
        ]  # fmt: skip
        for ip in refused:
            with self.subTest(ip):
                self.assertFalse(is_public(ipaddress.ip_address(ip)))
        public = ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "64:ff9b::808:808",
                  "2002:808:808::1")  # fmt: skip
        for ip in public:
            with self.subTest(ip):
                self.assertTrue(is_public(ipaddress.ip_address(ip)))

    def test_checked_before_every_request(self) -> None:
        ok = direct_url("https://example.com/npcs.pdf")
        check_url(ok)
        refused = {
            "http://example.com/": fetch.ONLY_HTTPS,
            "ftp://example.com/": fetch.NOT_A_LINK,
            "https://example.com:8443/": fetch.NOT_PUBLIC,
            "https://user:pw@example.com/": fetch.NOT_PUBLIC,
            "https://127.0.0.1/": fetch.NOT_PUBLIC,
            "https://169.254.169.254/latest/meta-data/": fetch.NOT_PUBLIC,
            "https://[::1]/": fetch.NOT_PUBLIC,
            "https://[::ffff:10.0.0.1]/": fetch.NOT_PUBLIC,
            "https://127.1/": fetch.NOT_PUBLIC,  # IP addresses written oddly
            "https://2130706433/": fetch.NOT_PUBLIC,
            "https://127.0.0.1./": fetch.NOT_PUBLIC,
        }
        for link, why in refused.items():
            with self.subTest(link), self.assertRaises(LinkError) as ctx:
                check_url(direct_url(link))
            self.assertEqual(str(ctx.exception), why)

    def test_sign_in_pages_of_file_hosts(self) -> None:
        page = yarl.URL("https://accounts.google.com/signin")
        wiki = yarl.URL("https://forgottenrealms.fandom.com/wiki/Auril")
        self.assertTrue(fetch.sign_in_page(".html", page, "docs.google.com", page.host or ""))
        # A short link that ends at a file host's sign-in page: every host on the way counts.
        self.assertTrue(fetch.sign_in_page(".html", page, "bit.ly", "accounts.google.com"))
        self.assertTrue(fetch.sign_in_page(".html", wiki, "www.dropbox.com"))
        self.assertFalse(fetch.sign_in_page(".pdf", page, "docs.google.com"))
        self.assertFalse(fetch.sign_in_page(".html", wiki, "forgottenrealms.fandom.com"))
        self.assertFalse(fetch.sign_in_page(".html", wiki, "notdropbox.com"))
        published = yarl.URL(f"https://docs.google.com/document/d/e/{DOC}/pub")
        self.assertFalse(fetch.sign_in_page(".html", published, "docs.google.com"))
        # Google Drive's page (private, or too big to scan) says to download it.
        self.assertEqual(fetch.sign_in_message("drive.google.com"), fetch.DRIVE_PAGE)
        self.assertEqual(fetch.sign_in_message("bit.ly", "docs.google.com"), fetch.NOT_SHARED)


class Resolver(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, *ips: str) -> list[Any]:
        resolver = fetch._PublicResolver(fetch.STRICT)
        found = [{"hostname": "h", "host": ip, "port": 443, "family": 2, "proto": 0,
                  "flags": 0} for ip in ips]  # fmt: skip

        async def fake(*_: Any) -> list[Any]:
            return found

        with patch.object(resolver._inner, "resolve", fake):
            return await resolver.resolve("h", 443)

    async def test_any_private_answer_refuses_the_host(self) -> None:
        self.assertEqual(len(await self.resolve("93.184.215.14")), 1)
        for ips in (("10.0.0.5",), ("93.184.215.14", "127.0.0.1"), ()):
            with self.subTest(ips), self.assertRaises(LinkError):
                await self.resolve(*ips)

    async def test_a_name_for_this_machine_is_refused_end_to_end(self) -> None:
        with self.assertRaises(LinkError) as ctx:
            await fetch.fetch("https://localhost/npcs.txt")  # resolves without a network
        self.assertEqual(str(ctx.exception), fetch.NOT_PUBLIC)


class Fetching(unittest.IsolatedAsyncioTestCase):
    """Against a local server, with a policy that lets this test reach it (and only it)."""

    async def asyncSetUp(self) -> None:
        self.routes: dict[str, Any] = {}

        async def handler(request: web.Request) -> web.StreamResponse:
            reply = self.routes[request.path](request)
            if inspect.isawaitable(reply):
                reply = await reply
            return reply  # type: ignore[no-any-return]

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

    def route(self, path: str, response: Any) -> None:
        self.routes[path] = lambda _request: response

    def redirect(self, path: str, to: str) -> None:
        # A new reply each time: aiohttp can't send one reply object twice.
        self.routes[path] = lambda _request: web.Response(status=302, headers={"Location": to})

    async def get(self, path: str) -> fetch.Fetched:
        return await fetch.fetch(self.base + path, self.policy)

    async def refused(self, path: str) -> str:
        with self.assertRaises(LinkError) as ctx:
            await self.get(path)
        self.assertNotIn(str(self.server.port), str(ctx.exception))  # never the link
        return str(ctx.exception)

    async def test_documents_and_pages_by_type(self) -> None:
        self.route("/a", web.Response(text="Auril", content_type="text/plain"))
        self.route("/b", web.Response(text="<p>Auril</p>", content_type="text/html"))
        self.route("/c", web.Response(body=b"%PDF", content_type="application/pdf"))
        self.route(
            "/d",
            web.Response(
                body=b"%PDF",
                content_type="application/octet-stream",
                headers={"Content-Disposition": 'attachment; filename="NPCs.PDF"'},
            ),
        )
        self.assertEqual(await self.get("/a"), fetch.Fetched(b"Auril", "link.txt"))
        self.assertEqual((await self.get("/b")).filename, "link.html")
        self.assertEqual((await self.get("/c")).filename, "link.pdf")
        self.assertEqual((await self.get("/d")).filename, "link.pdf")

    async def test_redirects_are_followed_and_checked(self) -> None:
        self.redirect("/one", "/two")
        self.redirect("/two", "/doc")
        self.route("/doc", web.Response(text="Auril", content_type="text/plain"))
        self.assertEqual((await self.get("/one")).data, b"Auril")
        # A redirect to an address the policy refuses (cloud metadata) stops at once.
        self.redirect("/meta", "http://169.254.169.254/latest/meta-data/")
        self.assertEqual(await self.refused("/meta"), fetch.NOT_PUBLIC)
        # Too many redirects: usually a sign-in loop.
        self.redirect("/loop", "/loop")
        self.assertEqual(await self.refused("/loop"), fetch.NOT_SHARED)

    async def test_problems_in_plain_words(self) -> None:
        self.route("/private", web.Response(status=403))
        self.route("/gone", web.Response(status=404))
        self.route("/broken", web.Response(status=500))
        self.route("/image", web.Response(body=b"x", content_type="image/png"))
        self.assertEqual(await self.refused("/private"), fetch.NOT_SHARED)
        self.assertEqual(await self.refused("/gone"), fetch.NOT_SHARED)
        self.assertEqual(await self.refused("/broken"), fetch.UNREACHABLE)
        self.assertEqual(await self.refused("/image"), fetch.WRONG_TYPE)

    async def test_too_big(self) -> None:
        self.route("/big", web.Response(text="x" * 5000, content_type="text/plain"))

        async def streamed(request: web.Request) -> web.StreamResponse:
            reply = web.StreamResponse(headers={"Content-Type": "text/plain"})
            reply.enable_chunked_encoding()  # no size given up front: counted as it comes
            await reply.prepare(request)
            for _ in range(10):
                await reply.write(b"x" * 500)
            return reply

        self.routes["/stream"] = streamed
        with patch.object(fetch, "MAX_BYTES", 1000):
            self.assertEqual(await self.refused("/big"), fetch.TOO_BIG)
            self.assertEqual(await self.refused("/stream"), fetch.TOO_BIG)

    async def test_compressed_replies_are_unpacked_a_piece_at_a_time(self) -> None:
        seen: dict[str, str] = {}

        def gzipped(body: bytes, encoding: str = "gzip") -> Any:
            def reply(request: web.Request) -> web.Response:
                seen["asked"] = request.headers.get("Accept-Encoding", "")
                return web.Response(
                    body=gzip.compress(body),
                    headers={"Content-Type": "text/plain", "Content-Encoding": encoding},
                )

            return reply

        self.routes["/small"] = gzipped(b"Auril")
        self.routes["/bomb"] = gzipped(b"\0" * (20 * 1024 * 1024))  # ~20 KB on the wire
        self.routes["/br"] = gzipped(b"Auril", encoding="br")
        self.assertEqual((await self.get("/small")).data, b"Auril")
        self.assertEqual(seen["asked"], "gzip")
        self.assertEqual(await self.refused("/bomb"), fetch.TOO_BIG)
        self.assertEqual(await self.refused("/br"), fetch.WRONG_TYPE)

    async def test_slow_and_odd_replies(self) -> None:
        async def slow(_request: web.Request) -> web.Response:
            await asyncio.sleep(5)
            return web.Response(text="late")

        self.routes["/slow"] = slow
        self.route("/nowhere", web.Response(status=302))  # a redirect without a Location
        with patch.object(fetch, "TIMEOUT_S", 0.2):
            self.assertEqual(await self.refused("/slow"), fetch.UNREACHABLE)
        self.assertEqual(await self.refused("/nowhere"), fetch.UNREACHABLE)

    async def test_nothing_listening(self) -> None:
        await self.server.close()
        self.assertEqual(await self.refused("/a"), fetch.UNREACHABLE)
