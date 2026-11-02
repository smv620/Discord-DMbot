"""The private bucket kept pictures live in (#3274): Cloudflare R2 through its S3-compatible API.

A small signed-request client over aiohttp (AWS Signature V4; aiohttp is already a dependency),
so core takes no cloud SDK. It does four things: put an object, get one, delete one, and list
the keys under a prefix (for the sweep that removes files no row points to). The keys and secret
come from `.env`, are never logged and never put in an error; a failure raises `BucketError`
carrying only the status code or the error class, because the words of a response could hold a
key name.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable

import aiohttp

REGION = "auto"  # R2 ignores the region but signs with this name
SERVICE = "s3"
TIMEOUT_S = 30.0


# (method, url, headers, body) -> (status, body). Swapped for a fake in tests.
Transport = Callable[[str, str, dict[str, str], bytes], Awaitable[tuple[int, bytes]]]


async def _aiohttp_transport(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, bytes]:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.request(method, url, headers=headers, data=body) as reply,
    ):
        return reply.status, await reply.read()


class BucketError(Exception):
    """A bucket call failed. The message is a status code or an error class, never a key."""


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def authorization(
    *,
    method: str,
    host: str,
    path: str,
    query: str,
    payload_hash: str,
    access_key: str,
    secret_key: str,
    now: dt.datetime,
    region: str = REGION,
    service: str = SERVICE,
    sign_content_hash: bool = True,
) -> dict[str, str]:
    """The signed headers for one request (Signature V4). Pure, so it can be tested against a
    known answer."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    headers = {"host": host, "x-amz-date": stamp}
    if sign_content_hash:  # S3 wants it signed; AWS's generic test vectors do not sign it
        headers["x-amz-content-sha256"] = payload_hash
    signed = ";".join(sorted(headers))
    canonical = "\n".join(
        [
            method,
            path,
            query,
            "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
            signed,
            payload_hash,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    k = _sign(("AWS4" + secret_key).encode(), day)
    for part in (region, service, "aws4_request"):
        k = _sign(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": stamp,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, "
            f"Signature={signature}"
        ),
    }


class Bucket:
    """One R2 bucket."""

    def __init__(
        self,
        account_id: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        transport: Transport | None = None,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self._host = f"{account_id}.r2.cloudflarestorage.com"
        self._bucket = bucket
        self._access = access_key
        self._secret = secret_key
        self._send = transport or _aiohttp_transport
        self._clock = clock

    def _path(self, key: str) -> str:
        return "/" + urllib.parse.quote(f"{self._bucket}/{key}", safe="/-_.~")

    async def _call(
        self,
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        ok: tuple[int, ...] = (200,),
    ) -> bytes:
        headers = authorization(
            method=method,
            host=self._host,
            path=path,
            query=query,
            payload_hash=hashlib.sha256(body).hexdigest(),
            access_key=self._access,
            secret_key=self._secret,
            now=self._clock(),
        )
        url = f"https://{self._host}{path}" + (f"?{query}" if query else "")
        try:
            status, content = await self._send(method, url, headers, body)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BucketError(type(exc).__name__) from None
        if status not in ok:
            raise BucketError(f"status {status}")
        return content

    async def put(self, key: str, data: bytes) -> None:
        await self._call("PUT", self._path(key), body=data, ok=(200,))

    async def get(self, key: str) -> bytes:
        return await self._call("GET", self._path(key))

    async def delete(self, key: str) -> None:
        """Deleting what is not there is not an error."""
        await self._call("DELETE", self._path(key), ok=(200, 204, 404))

    async def keys(self, prefix: str) -> list[str]:
        """Every key under a prefix (a campaign's folder), in pages."""
        return [key for key, _ in await self.objects(prefix)]

    async def objects(self, prefix: str) -> list[tuple[str, dt.datetime]]:
        """Every key under a prefix with when it was last written, in pages."""
        found: list[tuple[str, dt.datetime]] = []
        token = ""
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        while True:
            params = {"list-type": "2", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            query = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
            reply = await self._call("GET", "/" + self._bucket, query=query)
            try:
                root = ET.fromstring(reply)
                for item in root.findall("s:Contents", ns):
                    key = item.findtext("s:Key", "", ns) or ""
                    when = item.findtext("s:LastModified", "", ns) or ""
                    found.append((key, _when(when)))
            except (ET.ParseError, ValueError):
                raise BucketError("unreadable list") from None
            if (root.findtext("s:IsTruncated", "false", ns) or "false") != "true":
                return found
            token = root.findtext("s:NextContinuationToken", "", ns) or ""
            if not token:  # a partial list must never pass for the whole (the sweep trusts it)
                raise BucketError("list cut short")


def _when(text: str) -> dt.datetime:
    """A listing's time ("2026-11-02T12:00:00.000Z"); a missing one counts as just now, so the
    sweep never treats a file it cannot date as old."""
    if not text:
        return dt.datetime.now(dt.UTC)
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
