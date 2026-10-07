"""Website sign-in sessions and the OAuth sign-in check (#435).

- The session cookie holds a random token; the database keeps only its SHA-256, so a
  database copy can't be used to sign in.
- The sign-in check (`state`) is signed with WEB_SECRET_KEY, expires in 10 minutes, and
  must match a cookie set on the same browser, so nobody can sign someone else in to
  their own account (login CSRF).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.web.discord import DiscordGuild, DiscordUser

STATE_SECONDS = 600


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_state(secret: bytes, now: int) -> str:
    nonce = secrets.token_urlsafe(24)
    expires = now + STATE_SECONDS
    mac = hmac.new(secret, f"{nonce}.{expires}".encode(), hashlib.sha256).digest()
    return f"{nonce}.{expires}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def state_ok(secret: bytes, state: str, cookie: str | None, now: int) -> bool:
    """The state Discord sent back is ours, unexpired, and from this same browser.
    Anything malformed is simply not ok (never an error)."""
    try:
        if not cookie or not hmac.compare_digest(state.encode(), cookie.encode()):
            return False
        nonce, expires_raw, mac = state.split(".")
        expires = int(expires_raw)
        good = hmac.new(secret, f"{nonce}.{expires}".encode(), hashlib.sha256).digest()
        given = base64.urlsafe_b64decode(mac + "=" * (-len(mac) % 4))
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(good, given) and now < expires


@dataclass(frozen=True)
class Session:
    user_id: int
    display_name: str
    guilds: tuple[DiscordGuild, ...]
    expires_at: int


async def sign_in(
    db: Database,
    user: DiscordUser,
    guilds: list[DiscordGuild],
    *,
    now: int,
    days: int,
) -> str:
    """Record the person and start a session. Returns the cookie's token."""
    token = secrets.token_urlsafe(32)
    guilds_json = json.dumps(
        [{"id": str(g.id), "name": g.name, "manage": g.manage} for g in guilds]
    )
    async with db.user(user.id) as conn:
        await conn.execute(
            "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (user_id) DO UPDATE"
            " SET email = EXCLUDED.email, last_sign_in_at = EXCLUDED.last_sign_in_at",
            (user.id, user.email, now, now),
        )
        await conn.execute(
            "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at, guilds,"
            " display_name) VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
            (hash_token(token), user.id, now, now + days * 86400, guilds_json, user.name),
        )
    return token


async def find(db: Database, token: str, *, now: int) -> Session | None:
    """The unexpired session for this cookie, or None. (The database also hides expired
    sessions by its own clock; this checks the API's clock too.)"""
    id_hash = hash_token(token)
    async with db.session(id_hash) as conn:
        cur = await conn.execute(
            "SELECT user_id, display_name, guilds, expires_at FROM web_sessions WHERE id_hash = %s",
            (id_hash,),
        )
        row = await cur.fetchone()
    if row is None or row["expires_at"] <= now:
        return None
    guilds = tuple(
        DiscordGuild(id=int(g["id"]), name=str(g["name"]), manage=bool(g["manage"]))
        for g in row["guilds"]
    )
    return Session(
        user_id=row["user_id"],
        display_name=row["display_name"],
        guilds=guilds,
        expires_at=row["expires_at"],
    )


async def sign_out(db: Database, user_id: int, token: str) -> None:
    async with db.user(user_id) as conn:
        await conn.execute("DELETE FROM web_sessions WHERE id_hash = %s", (hash_token(token),))


async def delete_expired(db: Database) -> int:
    """Remove expired sessions. Returns how many."""
    async with db.cleanup() as conn:
        cur = await conn.execute("DELETE FROM web_sessions")
        return cur.rowcount
