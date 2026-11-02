"""The bucket client for kept pictures (#3274): signed requests, the four calls, and errors that
never carry a key. A fake transport stands in for R2; made-up keys only."""

from __future__ import annotations

import datetime as dt
import unittest

import aiohttp

from dmbot.config import ConfigError, load_settings
from dmbot.pictures.bucket import Bucket, BucketError, authorization

NOW = dt.datetime(2026, 11, 2, 12, 0, 0, tzinfo=dt.UTC)
ACCESS, SECRET = "AKIAEXAMPLE", "secret-example-value"


Call = tuple[str, str, dict[str, str], bytes]


def bucket(
    status: int = 200,
    body: bytes = b"",
    seen: list[Call] | None = None,
    raises: Exception | None = None,
) -> Bucket:
    async def transport(
        method: str, url: str, headers: dict[str, str], data: bytes
    ) -> tuple[int, bytes]:
        if seen is not None:
            seen.append((method, url, headers, data))
        if raises is not None:
            raise raises
        return status, body

    return Bucket("acct", "pics", ACCESS, SECRET, transport=transport, clock=lambda: NOW)


class Signing(unittest.TestCase):
    def sign(self, **over: str) -> dict[str, str]:
        args: dict[str, str] = {
            "method": "GET",
            "host": "acct.r2.cloudflarestorage.com",
            "path": "/pics/a.png",
            "query": "",
            "payload_hash": "0" * 64,
            "access_key": ACCESS,
            "secret_key": SECRET,
        }
        args.update(over)
        return authorization(
            method=args["method"],
            host=args["host"],
            path=args["path"],
            query=args["query"],
            payload_hash=args["payload_hash"],
            access_key=args["access_key"],
            secret_key=args["secret_key"],
            now=NOW,
        )

    def test_it_is_the_same_every_time_for_the_same_request(self) -> None:
        self.assertEqual(self.sign(), self.sign())

    def test_a_different_path_or_secret_signs_differently(self) -> None:
        base = self.sign()["Authorization"]
        self.assertNotEqual(base, self.sign(path="/pics/b.png")["Authorization"])
        self.assertNotEqual(base, self.sign(secret_key="other")["Authorization"])

    def test_it_names_the_scope_and_never_carries_the_secret(self) -> None:
        text = self.sign()["Authorization"]
        self.assertIn(f"Credential={ACCESS}/20261102/auto/s3/aws4_request", text)
        self.assertNotIn(SECRET, text)
        self.assertEqual(self.sign()["x-amz-date"], "20261102T120000Z")


class PublishedVector(unittest.TestCase):
    def test_aws_get_vanilla_signature(self) -> None:
        # AWS's published Signature V4 test suite, "get-vanilla": the signature must match exactly,
        # so the signing is proven and not just round-tripped.
        got = authorization(
            method="GET",
            host="example.amazonaws.com",
            path="/",
            query="",
            payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            access_key="AKIDEXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            now=dt.datetime(2015, 8, 30, 12, 36, 0, tzinfo=dt.UTC),
            region="us-east-1",
            service="service",
            sign_content_hash=False,
        )
        self.assertEqual(
            got["Authorization"],
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
            "SignedHeaders=host;x-amz-date, "
            "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
        )


class Calls(unittest.IsolatedAsyncioTestCase):
    async def test_put_sends_a_signed_request_to_the_key(self) -> None:
        seen: list[Call] = []
        await bucket(seen=seen).put("1/c/abc.png", b"data")
        [(method, url, headers, data)] = seen
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "https://acct.r2.cloudflarestorage.com/pics/1/c/abc.png")
        self.assertEqual(data, b"data")
        self.assertIn("AWS4-HMAC-SHA256", headers["Authorization"])

    async def test_get_returns_the_bytes(self) -> None:
        self.assertEqual(await bucket(body=b"picture").get("k"), b"picture")

    async def test_deleting_what_is_not_there_is_fine(self) -> None:
        await bucket(status=404).delete("gone")

    async def test_a_failure_carries_only_the_status(self) -> None:
        with self.assertRaises(BucketError) as caught:
            await bucket(status=403, body=b"key 1/c/abc.png no").put("1/c/abc.png", b"x")
        self.assertEqual(str(caught.exception), "status 403")

    async def test_a_network_failure_carries_only_the_error_class(self) -> None:
        failing = aiohttp.ClientConnectionError("secret-example-value in the message")
        with self.assertRaises(BucketError) as caught:
            await bucket(raises=failing).get("k")
        self.assertEqual(str(caught.exception), "ClientConnectionError")

    async def test_keys_are_listed_across_pages(self) -> None:
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        pages = [
            f"<ListBucketResult {ns}><IsTruncated>true</IsTruncated>"
            "<NextContinuationToken>t1</NextContinuationToken>"
            "<Contents><Key>1/c/a.png</Key>"
            "<LastModified>2026-11-02T10:00:00.000Z</LastModified></Contents></ListBucketResult>",
            f"<ListBucketResult {ns}><IsTruncated>false</IsTruncated>"
            "<Contents><Key>1/c/b.png</Key></Contents></ListBucketResult>",
        ]
        seen: list[Call] = []

        async def transport(
            method: str, url: str, headers: dict[str, str], data: bytes
        ) -> tuple[int, bytes]:
            seen.append((method, url, headers, data))
            return 200, pages[len(seen) - 1].encode()

        b = Bucket("acct", "pics", ACCESS, SECRET, transport=transport, clock=lambda: NOW)
        self.assertEqual(await b.keys("1/c/"), ["1/c/a.png", "1/c/b.png"])
        self.assertIn("prefix=1%2Fc%2F", seen[0][1])
        self.assertIn("continuation-token=t1", seen[1][1])


BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}
R2 = {
    "R2_ACCOUNT_ID": "a",
    "R2_ACCESS_KEY_ID": "b",
    "R2_SECRET_ACCESS_KEY": "c",
    "R2_BUCKET": "d",
}


class TheQuery(unittest.IsolatedAsyncioTestCase):
    """The signed query must be the encoded one SigV4 wants: every character but A-Z a-z 0-9
    - _ . ~ as %XX, so a "/" is %2F (a guild/campaign prefix) and a base64 token's "+", "/" and
    "=" are %2B, %2F and %3D."""

    async def test_a_prefix_and_a_base64_token_are_fully_encoded_and_sorted(self) -> None:
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        pages = [
            f"<ListBucketResult {ns}><IsTruncated>true</IsTruncated>"
            "<NextContinuationToken>ab/+==</NextContinuationToken></ListBucketResult>",
            f"<ListBucketResult {ns}><IsTruncated>false</IsTruncated></ListBucketResult>",
        ]
        urls: list[str] = []

        async def transport(
            method: str, url: str, headers: dict[str, str], data: bytes
        ) -> tuple[int, bytes]:
            urls.append(url)
            return 200, pages[len(urls) - 1].encode()

        b = Bucket("acct", "pics", ACCESS, SECRET, transport=transport, clock=lambda: NOW)
        await b.keys("12/ab c/")
        self.assertEqual(urls[0].split("?", 1)[1], "list-type=2&prefix=12%2Fab%20c%2F")
        self.assertEqual(
            urls[1].split("?", 1)[1],
            "continuation-token=ab%2F%2B%3D%3D&list-type=2&prefix=12%2Fab%20c%2F",
        )


class ShortList(unittest.IsolatedAsyncioTestCase):
    async def test_objects_carry_when_they_were_written(self) -> None:
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        page = (
            f"<ListBucketResult {ns}><IsTruncated>false</IsTruncated>"
            "<Contents><Key>p/a.png</Key><LastModified>2026-11-02T10:00:00.000Z</LastModified>"
            "</Contents></ListBucketResult>"
        )
        got = await bucket(body=page.encode()).objects("p/")
        self.assertEqual(got, [("p/a.png", dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.UTC))])

    async def test_a_cut_list_without_a_token_is_an_error_not_a_short_answer(self) -> None:
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        page = f"<ListBucketResult {ns}><IsTruncated>true</IsTruncated></ListBucketResult>"
        with self.assertRaises(BucketError):
            await bucket(body=page.encode()).keys("1/c/")


class Setting(unittest.TestCase):
    def test_kept_pictures_are_off_with_500_megabytes_by_default(self) -> None:
        got = load_settings(BASE)
        self.assertFalse(got.pictures_kept)
        self.assertFalse(got.pictures_ready)
        self.assertEqual(got.picture_storage_mb, 500)

    def test_they_need_the_switch_and_all_four_values(self) -> None:
        on = {**BASE, **R2, "DMBOT_PICTURES_KEPT": "1"}
        self.assertTrue(load_settings(on).pictures_ready)
        self.assertFalse(load_settings({**BASE, **R2}).pictures_ready)  # switch off
        for missing in R2:
            partial = {k: v for k, v in on.items() if k != missing}
            self.assertFalse(load_settings(partial).pictures_ready, missing)

    def test_the_storage_is_a_setting(self) -> None:
        self.assertEqual(
            load_settings({**BASE, "DMBOT_PICTURE_STORAGE_MB": "1000"}).picture_storage_mb, 1000
        )
        for bad in ("0", "x", "-5", "2000000"):
            with self.assertRaises(ConfigError):
                load_settings({**BASE, "DMBOT_PICTURE_STORAGE_MB": bad})

    def test_the_keys_never_show_in_the_settings_text(self) -> None:
        text = repr(load_settings({**BASE, **R2, "R2_SECRET_ACCESS_KEY": "hush-value"}))
        self.assertNotIn("hush-value", text)


if __name__ == "__main__":
    unittest.main()
