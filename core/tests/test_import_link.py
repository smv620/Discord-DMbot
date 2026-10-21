"""Reading a transcript from a Google Doc link (#2140), with a fake fetch. Invented ids only."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dmbot import fetch
from dmbot.transcript import import_link as il

DOC = "https://docs.google.com/document/d/" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
TEXT = "Mira: We open the door and listen.\n"


def got(data: bytes, name: str = "link.txt") -> fetch.Fetched:
    return fetch.Fetched(data, name)


class TheReader(unittest.IsolatedAsyncioTestCase):
    async def read(self, link: object, result: object = None) -> str:
        fake = (
            AsyncMock(side_effect=result)
            if isinstance(result, Exception)
            else AsyncMock(return_value=result or got(TEXT.encode()))
        )
        with patch.object(fetch, "fetch", fake):
            return await il.read_doc(link)

    async def test_a_shared_doc_gives_its_text(self) -> None:
        self.assertEqual(await self.read(DOC + "/edit?usp=sharing"), TEXT)

    async def test_the_reader_is_called_with_the_two_megabyte_cap_and_google_hosts_only(
        self,
    ) -> None:
        fake = AsyncMock(return_value=got(TEXT.encode()))
        with patch.object(fetch, "fetch", fake):
            await il.read_doc(DOC)
        assert fake.await_args is not None
        kw = fake.await_args.kwargs
        self.assertEqual(kw["max_bytes"], 2 * 1024 * 1024)
        self.assertIn("docs.google.com", kw["stay_on"])
        self.assertNotIn("example.com", kw["stay_on"])

    async def test_anything_but_a_google_doc_link_is_refused_without_fetching(self) -> None:
        for link in (
            "https://example.com/document/d/" + "a" * 30,
            "https://drive.google.com/file/d/" + "a" * 30,
            "not a link",
            "",
            None,
            7,
            DOC + "x" * 3000,
        ):
            fake = AsyncMock()
            with patch.object(fetch, "fetch", fake), self.assertRaises(il.DocError) as err:
                await il.read_doc(link)
            self.assertEqual(err.exception.code, il.NOT_A_GOOGLE_DOC, link)
            fake.assert_not_awaited()

    async def test_the_readers_failures_become_the_four_codes(self) -> None:
        cases = {
            fetch.NotShared("x"): il.NOT_SHARED,
            fetch.TooBig("x"): il.TOO_BIG,
            fetch.Unreachable("x"): il.UNREACHABLE,
            fetch.LinkError("x"): il.UNREACHABLE,
        }
        for error, code in cases.items():
            with self.assertRaises(il.DocError) as err:
                await self.read(DOC, error)
            self.assertEqual(err.exception.code, code)

    async def test_something_that_is_not_plain_text_is_unreachable(self) -> None:
        for bad in (got(b"<html>", "link.html"), got(b"\xff\xfe\x00bad")):
            with self.assertRaises(il.DocError) as err:
                await self.read(DOC, bad)
            self.assertEqual(err.exception.code, il.UNREACHABLE)


class TheFetchChanges(unittest.TestCase):
    def test_a_google_doc_link_has_an_export_address_and_others_do_not(self) -> None:
        url = fetch.google_doc_export(DOC + "/edit")
        assert url is not None
        self.assertTrue(url.path.endswith("/export"))
        self.assertEqual(url.query["format"], "txt")
        self.assertIsNone(fetch.google_doc_export("https://example.com/x"))
        for other in (  # same host, not a document's text export
            "https://docs.google.com/spreadsheets/d/" + "a" * 30 + "/export?format=csv",
            "https://docs.google.com/forms/d/" + "a" * 30 + "/export?format=txt",
        ):
            self.assertIsNone(fetch.google_doc_export(other), other)
        self.assertIsNone(fetch.google_doc_export("nonsense"))


if __name__ == "__main__":
    unittest.main()
