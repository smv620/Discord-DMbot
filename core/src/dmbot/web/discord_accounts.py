"""The Account page's Discord accounts (#4473, #2534; the add and remove are #2533's slice 4, whose
shared functions and words live in `dmbot.accounts`): the list the page shows, the opaque key each
one is removed by, and the two calls the routes make.

The page never sees a Discord number. Each account has a key made from the signed-in account's
number and that Discord account's, signed with the site's secret, so a key means nothing to anyone
else and cannot be guessed. Only the account being used now has a name (its Discord display name);
an added one shows the day it was added, because the website keeps no name for a Discord account
that has never signed in here (the database refuses it a row of its own, `dmbot_web_users_id`).
Words and keys only."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from dmbot.db import Database
from dmbot.web.accounts import LINK_WORDS, UNLINK_WORDS, LinkResult, Login
from dmbot.web.sessions import Session

NOT_FOUND = "not_found"  # no account here has that key (also for another DMbot account's)


async def logins_of(db: Database, session: Session) -> list[Login]:
    """The Discord accounts of the signed-in account that still sign in, first first. The website's
    role has no right on the tables behind this, so it asks the door `dmbot_web_logins_list`
    (migration 0506): the same list `dmbot.accounts.logins_of` gives the bot."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        cur = await conn.execute("SELECT dmbot_web_logins_list(%s) AS r", (session.user_id,))
        row = await cur.fetchone()
    got = row["r"] if row else None
    if got is None:
        return []
    added = [Login(int(a["id"]), False, int(a["at"])) for a in got.get("added", [])]
    return added if got.get("firstOff") else [Login(session.user_id, True, None), *added]


async def _door(
    db: Database, session: Session, door: str, discord_id: int, now: int, words: dict[str, str]
) -> LinkResult:
    """One of the two doors that put a Discord account on or take it off the signed-in account,
    answered in `dmbot.accounts`' own words (an unknown code gets the sign-in words, never a raw
    code)."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        cur = await conn.execute(f"SELECT {door}(%s, %s) AS r", (int(discord_id), int(now)))
        row = await cur.fetchone()
    code = "not_yours" if row is None else str(row["r"])
    if code == "ok":
        return LinkResult(True, code, "")
    return LinkResult(False, code, words.get(code, words["not_yours"]))


def key_of(secret: bytes, account: int, discord_id: int) -> str:
    """The handle the page removes an account by: never the Discord number."""
    mac = hmac.new(secret, f"discord-account.{account}.{discord_id}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode().rstrip("=")[:32]


def _name(login: Login, session: Session) -> str:
    if login.discord_id == session.user_id:
        return session.display_name
    added = datetime.fromtimestamp(login.added_at or 0, UTC)
    return f"Discord account added on {added.day} {added:%B %Y}"


async def listing(db: Database, session: Session, secret: bytes) -> list[dict[str, Any]]:
    """The account's Discord accounts for `GET /me`: a key, a name, whether it is the one in use,
    and whether it can be taken off (never the one in use, never the last)."""
    logins = await logins_of(db, session)
    return [
        {
            "key": key_of(secret, session.user_id, login.discord_id),
            "name": _name(login, session),
            "current": login.discord_id == session.user_id,
            "canRemove": len(logins) > 1 and login.discord_id != session.user_id,
        }
        for login in logins
    ]


async def add(db: Database, session: Session, discord_id: int, *, now: int) -> str:
    """The result code the page is sent back with after the person signed in as the Discord account
    to add: `added`, `already`, or the refusal's code (`has_campaigns`, `has_plan`, `taken`)."""
    if any(login.discord_id == discord_id for login in await logins_of(db, session)):
        return "already"
    result = await _door(db, session, "dmbot_web_account_link", discord_id, now, LINK_WORDS)
    if result.ok:
        return "added"
    # `has_account` and `taken` have the same words for the person: it is on another account
    code = {"has_account": "taken"}.get(result.code, result.code)
    # (only the codes the page has words for ever reach the address)
    return code if code in ("has_campaigns", "has_plan", "taken") else "failed"


async def remove(
    db: Database, session: Session, secret: bytes, key: str, *, now: int
) -> LinkResult:
    """Take the Discord account with this key off the signed-in account. Never the one in use
    (the page never offers it, and it is refused here too) and never the last: the shared
    function says so with its own words."""
    found = next(
        (
            login
            for login in await logins_of(db, session)
            if hmac.compare_digest(key_of(secret, session.user_id, login.discord_id), key)
        ),
        None,
    )
    if found is None:
        return LinkResult(False, NOT_FOUND, "")
    if found.discord_id == session.user_id:
        return LinkResult(False, "in_use", "")
    return await _door(db, session, "dmbot_web_account_unlink", found.discord_id, now, UNLINK_WORDS)
