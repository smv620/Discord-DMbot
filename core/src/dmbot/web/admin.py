"""The admin sign-in (#772): who may open the admin page, and how.

Rules (docs/PLAN.md, "Free access and the admin page"; #772):
- Only addresses in ADMIN_EMAILS may sign in: with Google (a verified email only), or with
  that email and the admin password, whose argon2id hash is in the server settings, never
  in the database or the repository.
- Five wrong tries in 15 minutes lock that email and that connection for 15 minutes. A
  wrong email, a wrong password and a locked try all get the same answer.
- The admin session is its own cookie (HttpOnly, Secure, SameSite=Strict), kept in memory
  here (one admin, one API process: a restart only signs the admin out). It ends after an
  hour idle and 12 hours at most, and every admin POST carries its CSRF token.
- Logs name the connection at most: never an email, a password or a token.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import aiohttp
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

IDLE_SECONDS = 3600
MAX_SECONDS = 12 * 3600
FAIL_WINDOW = 15 * 60
FAIL_LIMIT = 5
LOCK_SECONDS = 15 * 60
MAX_SESSIONS = 20  # one admin; a few browsers at most
MIN_PASSWORD = 16
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CSRF_HEADER = "X-Admin-CSRF"

_hasher = PasswordHasher()  # argon2id with the library's current recommended costs
# Checked when no hash is set or the email is wrong, so every try costs the same time.
_DECOY = _hasher.hash(secrets.token_urlsafe(32))


class AdminError(RuntimeError):
    """Google refused or didn't answer. The message holds no token or email."""


# Passwords


def encode_hash(argon2_hash: str) -> str:
    """The form kept in .env: base64, because an argon2 hash is full of `$` signs, which
    Docker Compose would try to expand when it reads the file."""
    return base64.urlsafe_b64encode(argon2_hash.encode("ascii")).decode("ascii")


def decode_hash(encoded: str) -> str | None:
    """The argon2id hash from its .env form, or None if it isn't one."""
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("ascii")
    except (ValueError, UnicodeError, binascii.Error):
        return None
    return raw if raw.startswith("$argon2id$") else None


def hash_password(password: str) -> str:
    """For scripts/set-admin-password: the .env form of the password's hash."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"The password needs {MIN_PASSWORD} characters or more.")
    return encode_hash(_hasher.hash(password))


def password_matches(encoded_hash: str, password: str) -> bool:
    argon2_hash = decode_hash(encoded_hash) if encoded_hash else None
    try:
        return _hasher.verify(argon2_hash or _DECOY, password) and argon2_hash is not None
    except (Argon2Error, InvalidHashError):
        return False


def email_allowed(email: str, allowed: tuple[str, ...]) -> bool:
    """Whether this is an admin address, compared in constant time."""
    wanted = email.strip().lower().encode("utf-8")
    found = False
    for each in allowed:
        found |= hmac.compare_digest(wanted, each.encode("utf-8"))
    return found


# Wrong tries


@dataclass
class FailedTries:
    """Five failures in 15 minutes lock a key (an email, a connection) for 15 minutes.
    In memory: the API is one process, and forgetting on a restart does no harm."""

    clock: Callable[[], float] = time.monotonic
    most_keys: int = 10_000  # a flood of made-up emails can't grow this without end
    _fails: dict[str, list[float]] = field(default_factory=dict)
    _locked: dict[str, float] = field(default_factory=dict)

    def locked(self, key: str) -> bool:
        until = self._locked.get(key)
        if until is None:
            return False
        if self.clock() < until:
            return True
        del self._locked[key]
        return False

    def fail(self, key: str) -> None:
        now = self.clock()
        recent = [t for t in self._fails.get(key, []) if now - t < FAIL_WINDOW]
        recent.append(now)
        if len(recent) >= FAIL_LIMIT:
            self._locked[key] = now + LOCK_SECONDS
            self._fails.pop(key, None)
            return
        if key not in self._fails and len(self._fails) >= self.most_keys:
            self._fails.pop(next(iter(self._fails)))  # drop the oldest key
        self._fails[key] = recent

    def clear(self, key: str) -> None:
        self._fails.pop(key, None)


# Sessions


@dataclass
class AdminSession:
    email: str
    created: float
    seen: float
    csrf: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass
class AdminSessions:
    clock: Callable[[], float] = time.monotonic
    _by_hash: dict[str, AdminSession] = field(default_factory=dict)

    def start(self, email: str) -> str:
        """A new session; returns the cookie's token (only its hash is kept)."""
        now = self.clock()
        while len(self._by_hash) >= MAX_SESSIONS:
            self._by_hash.pop(next(iter(self._by_hash)))  # the oldest goes
        token = secrets.token_urlsafe(32)
        self._by_hash[_hash(token)] = AdminSession(
            email=email, created=now, seen=now, csrf=secrets.token_urlsafe(32)
        )
        return token

    def find(self, token: str | None) -> AdminSession | None:
        """The live session for this cookie, kept alive by the visit; None if it ended."""
        if not token:
            return None
        try:
            key = _hash(token)
        except UnicodeError:
            return None
        session = self._by_hash.get(key)
        if session is None:
            return None
        now = self.clock()
        if now - session.seen >= IDLE_SECONDS or now - session.created >= MAX_SECONDS:
            del self._by_hash[key]
            return None
        session.seen = now
        return session

    def end(self, token: str | None) -> None:
        if token:
            with contextlib.suppress(UnicodeError):
                self._by_hash.pop(_hash(token), None)


def csrf_ok(session: AdminSession, sent: str | None) -> bool:
    return sent is not None and hmac.compare_digest(session.csrf, sent)


# Google


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def id_token_claims(id_token: str) -> dict[str, Any]:
    """The ID token's claims. Not signature-checked: it came straight from Google's token
    endpoint over TLS with our client secret, which OpenID Connect allows in place of the
    signature (Core 1.0, 3.1.3.7). Its claims are still checked (`verified_email`)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, binascii.Error) as exc:
        raise AdminError("Google's ID token couldn't be read") from exc
    if not isinstance(claims, dict):
        raise AdminError("Google's ID token couldn't be read")
    return claims


def verified_email(
    claims: dict[str, Any], *, client_id: str, nonce: str, now: float, allowed: tuple[str, ...]
) -> str | None:
    """The admin's email if Google vouches for it, for us, for this sign-in; else None."""
    email = claims.get("email")
    try:
        fresh = float(claims.get("exp", 0)) > now
    except (TypeError, ValueError):
        fresh = False
    ok = (
        claims.get("iss") in GOOGLE_ISSUERS
        and claims.get("aud") == client_id
        and fresh
        and isinstance(claims.get("nonce"), str)
        and hmac.compare_digest(claims["nonce"], nonce)
        and claims.get("email_verified") is True
        and isinstance(email, str)
        and email_allowed(email, allowed)
    )
    return email.strip().lower() if ok and isinstance(email, str) else None


class GoogleSignIn(Protocol):
    def authorize_url(
        self, *, state: str, nonce: str, challenge: str, redirect_uri: str
    ) -> str: ...

    async def claims(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        """The ID token's claims for this sign-in code."""
        ...


class HttpGoogle:
    """Google's OpenID Connect, authorisation-code flow with PKCE; scopes `openid email`."""

    def __init__(self, client_id: str, client_secret: str, *, token_url: str = GOOGLE_TOKEN):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    def authorize_url(self, *, state: str, nonce: str, challenge: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "scope": "openid email",
                "redirect_uri": redirect_uri,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTHORIZE}?{query}"

    async def claims(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._http.post(
                self._token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            ) as response:
                if response.status >= 400:
                    raise AdminError(f"Google answered {response.status}")
                raw = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise AdminError(f"Couldn't reach Google ({type(exc).__name__})") from exc
        token = raw.get("id_token") if isinstance(raw, dict) else None
        if not isinstance(token, str):
            raise AdminError("Google's answer had no ID token")
        return id_token_claims(token)
