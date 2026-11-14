"""Kept pictures on the website (#3274): the open link and the campaign's list.

Two routes live on this module:

* `GET /pictures/{code}` (and `/preview`): the link anyone can open in a new tab. The code is the
  whole key (256 random bits). A public picture answers with a short redirect to a signed address
  of the private bucket, good for a few minutes, so the link itself never expires. A private one
  answers the same way only to a signed-in person whose server holds the campaign. A removed one,
  and a private one for anyone else, answer with a few plain words and nothing else: no poster, no
  description, no campaign. A code that names nothing is one `not_found`.
* `GET /me/campaigns/{ref}/pictures`: the gallery, for any member of the campaign's server.

The website's database role has no right on `campaign_pictures`: everything comes from two doors
(`dmbot_picture_by_code`, `dmbot_web_pictures`, migration 0319). Nothing is ever written to the
bucket from here, and the keys of the files never leave this module; changes (private, public,
permanent, delete) are queued for the bot, which applies them with its own locks and limits.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.pictures.store import MB, permanent_limit_bytes
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session
from dmbot.web.transcripts import numbered_sessions, reads_campaign

_CODE = re.compile(r"[A-Za-z0-9_-]{43,}")
LINK_SECONDS = 300  # how long a signed address lasts: long enough to load, too short to pass on
PAGE = 24

# The only words an unopened link says (the page shows these and nothing about the table).
PRIVATE_WORDS = "This picture is private to its campaign."
REMOVED_WORDS = "This picture was removed."
REMOVED_FOR_ROOM_WORDS = "This picture was removed to make room for newer ones."
NOT_OPEN_WORDS = "Pictures are not open yet. Try again later."


def presign_get(
    *,
    account: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    now: dt.datetime,
    seconds: int = LINK_SECONDS,
) -> str:
    """A signed address for reading one object of the bucket until `seconds` have passed (AWS
    Signature V4, query form; R2's S3 API takes it). Pure, so it is tested against a fixed time."""
    host = f"{account}.r2.cloudflarestorage.com"
    path = "/" + urllib.parse.quote(f"{bucket}/{key}", safe="/-_.~")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    scope = f"{day}/auto/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(seconds),
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(params.items())
    )
    canonical = "\n".join(["GET", path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    k = hmac.new(("AWS4" + secret_key).encode(), day.encode(), hashlib.sha256).digest()
    for part in ("auto", "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{query}&X-Amz-Signature={signature}"


@dataclass(frozen=True, slots=True)
class Link:
    """What the open link does: `redirect` (to `url`), or one of the plain-word answers, or
    `not_open` when this server has no bucket key yet."""

    kind: str  # "redirect" | "private" | "removed" | "not_open"
    url: str = ""
    words: str = ""


async def resolve_link(
    db: Database,
    code: str,
    session: Session | None,
    *,
    sign: Callable[[str], str] | None,
    preview: bool = False,
) -> Link | None:
    """The answer for `/pictures/<code>`, or None for a code that names nothing. `sign` turns a
    file key into a short-lived address (None when the bucket key is not set)."""
    if not _CODE.fullmatch(code):
        return None
    async with db.anonymous() as conn:
        cur = await conn.execute("SELECT dmbot_picture_by_code(%s) AS r", (code,))
        found = (await cur.fetchone() or {}).get("r")
    if found is None:
        return None
    if found["removed"]:
        words = REMOVED_FOR_ROOM_WORDS if found["forRoom"] else REMOVED_WORDS
        return Link("removed", words=words)
    if not found["public"]:
        member = session is not None and any(
            str(g.id) == str(found["guild"]) for g in session.guilds
        )
        if not member:
            return Link("private", words=PRIVATE_WORDS)
    key = str(found["previewKey"] if preview and found["previewKey"] else found["key"])
    if sign is None or not key:
        return Link("not_open", words=NOT_OPEN_WORDS)
    return Link("redirect", url=sign(key))


def _cursor(raw: object) -> tuple[int, int]:
    """The page cursor: a picture's `cursor` ("made-at:id", which pages exactly) or just a made-at
    number. Anything else, or a number too big for the database, starts from the top."""
    made, _, ident = str(raw or "0").partition(":")
    try:
        at, row = int(made), int(ident or 0)
    except ValueError:
        return 0, 0
    if not (0 <= at < 2**62 and 0 <= row < 2**62):
        return 0, 0
    return at, row


def _bytes_to_mb(n: int) -> int:
    return -(-int(n) // MB)  # rounded up, so a picture never reads as no storage used


async def read_pictures(
    db: Database,
    session: Session,
    ref: str,
    *,
    session_number: object = None,
    poster: str = "",
    q: str = "",
    before: object = 0,
    storage_mb: int,
    api_url: str,
) -> dict[str, Any]:
    """The gallery page: `{pictures, more, isDm, usage, sessions, posters}`, newest first. A
    picture is `{code, at, sessionNumber, poster, description, previewUrl, permanent, isPublic,
    mine}`. `usage` is for the campaign's DMs only. Any member of the server may ask; anyone else
    gets `not_allowed`. The session filter is the transcripts page's number."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        listed = await numbered_sessions(conn, guild_id, campaign_id)
        numbers = {int(s["startedAt"]): int(s["number"]) for s in listed}
        by_number = {n: at for at, n in numbers.items()}
        wanted = 0
        if session_number not in (None, ""):
            try:
                wanted = by_number.get(int(str(session_number)), -1)
            except ValueError:
                wanted = -1
        after, after_id = _cursor(before)
        cur = await conn.execute(
            "SELECT dmbot_web_pictures(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, wanted, poster[:80], q[:200], after, after_id, PAGE),
        )
        got = (await cur.fetchone() or {}).get("r")
        people = await reads_campaign(conn, guild_id, campaign_id)
    if got is None:
        raise RecapError("not_allowed")
    if not people:
        # Poster names and image descriptions come from the transcripts, so they follow the
        # campaign's-people rule (#3891, #4403): anyone else sees only the pictures they posted
        # themselves, and no list of posters. (A picture's open link is shareable by design.)
        got = {
            **got,
            "pictures": [p for p in got["pictures"] if p["mine"]],
            "more": False,
            "posters": [],
            "sessions": [],
        }
    base = api_url.rstrip("/")
    usage = got.get("usage")
    return {
        "pictures": [
            {
                "code": p["code"],
                "at": int(p["at"]),
                "cursor": str(p["cursor"]),
                "sessionNumber": numbers.get(int(p["sessionAt"])) if p["sessionAt"] else None,
                "poster": p["poster"],
                "description": p["description"],
                "previewUrl": f"{base}/pictures/{p['code']}/preview",
                "permanent": bool(p["permanent"]),
                "isPublic": bool(p["public"]),
                "mine": bool(p["mine"]),
            }
            for p in got["pictures"]
        ],
        "more": bool(got["more"]),
        "isDm": bool(got["isDm"]),
        "usage": None
        if usage is None
        else {
            "usedMb": _bytes_to_mb(usage["usedBytes"]),
            "limitMb": storage_mb,
            "permanentMb": _bytes_to_mb(usage["permanentBytes"]),
            "permanentLimitMb": permanent_limit_bytes(storage_mb) // MB,
        },
        "sessions": sorted(
            {numbers[int(a)] for a in got["sessions"] if int(a) in numbers}, reverse=True
        ),
        "posters": sorted(str(p) for p in got["posters"]),
    }


class PictureRefused(Exception):
    """The bot or the door said no. `code` is for the page (`permanent-full` has its own words
    there); `words` is what to show."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(code)
        self.code = code
        self.words = words


ACTIONS = ("public", "private", "permanent", "unpermanent", "delete")
WAIT_SECONDS = 8.0  # how long a click waits for the bot before the page is told "working on it"
_REFUSALS = {
    "not_found": "That picture isn't kept any more.",
    "not_allowed": "You can't do that to this picture.",
    "bad_action": "That isn't something this page can do.",
    "too_soon": "Too many changes are waiting. Try again in a minute.",
}


async def act_on_picture(
    db: Database,
    session: Session,
    ref: str,
    code: str,
    action: str,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    wait: float = WAIT_SECONDS,
) -> dict[str, Any]:
    """Queue a picture's Make private/public, Permanent, Not permanent or Delete for the bot, then
    wait a few seconds for its answer. Returns `{state, note}`: `done`, or `waiting` when the bot
    has not answered yet (the change still happens). A refusal or failure raises `PictureRefused`
    with the words to show; the poster or a game master may ask (a game master only, for permanent).
    The bot checks again from the database before it changes anything."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _CODE.fullmatch(code):
        raise PictureRefused("not_found", _REFUSALS["not_found"])
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_picture_act(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, code, action),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise PictureRefused(got["status"], _REFUSALS.get(got["status"], "Couldn't do that."))
    started = time.monotonic()  # (a deadline, not a count of sleeps: each poll takes time too)
    while True:
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            cur = await conn.execute(
                "SELECT dmbot_web_picture_action(%s, %s, %s) AS r",
                (guild_id, campaign_id, got["id"]),
            )
            state = (await cur.fetchone() or {}).get("r") or {"state": "failed", "note": ""}
        if state["state"] == "done":
            return {"state": "done", "note": str(state["note"])}
        if state["state"] in ("refused", "failed"):
            words = str(state["note"]) or "Couldn't do that. Try again."
            raise PictureRefused(
                "permanent-full" if words.startswith("Permanent pictures are full") else "refused",
                words,
            )
        if time.monotonic() - started >= wait:
            return {"state": "waiting", "note": ""}
        await (sleep or asyncio.sleep)(0.5)
