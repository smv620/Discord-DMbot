"""The admin page's routes: sign-in (#772: Google, email and password, who is signed in,
sign out; the rules are in dmbot.web.admin) and free access (#773: the list, give or
change, revoke; the rules are in dmbot.web.grants).

Every POST already needs the website's own header and origin (the guard in dmbot.web.app);
admin POSTs after sign-in also need the session's CSRF token.
"""

# No `from __future__ import annotations` here: FastAPI must see the session types inside
# router() as real objects, or it reads them as query parameters.

import asyncio
import calendar
import contextlib
import datetime
import json
import logging
import re
import secrets
from collections.abc import Callable
from typing import Annotated, Any, TypeGuard

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from dmbot.db import Database
from dmbot.entitlements import GRANT_LEVELS
from dmbot.web import grants, tokens
from dmbot.web.admin import (
    CSRF_HEADER,
    MAX_SECONDS,
    SIGN_IN_SECONDS,
    AdminError,
    AdminSession,
    AdminSessions,
    FailedTries,
    GoogleSignIn,
    UsedStates,
    csrf_ok,
    email_allowed,
    password_matches,
    pkce_challenge,
    verified_email,
)
from dmbot.web.feedback import rate_key
from dmbot.web.settings import WebSettings

log = logging.getLogger(__name__)

MAX_BODY = 4 * 1024
# The same answer for a wrong email, a wrong password and a locked try (#772).
WRONG = "wrong_sign_in"
# A Discord user id: a snowflake, 17 to 20 digits (#773).
DISCORD_ID = re.compile(r"[0-9]{17,20}")
RECENT_CHANGES = 50
# Password tries waiting for the one before them; more than this is a flood, answered
# "busy" (not WRONG: the owner's own try in a flood mustn't say their password is wrong).
MAX_WAITING = 8


def router(
    settings: WebSettings,
    *,
    db: Database,
    google: GoogleSignIn | None,
    admin_sessions: AdminSessions,
    tries: FailedTries,
    client_address: Callable[[Request], str],
    clock: Callable[[], int],
) -> APIRouter:
    api = APIRouter(prefix="/admin")
    checking = asyncio.Semaphore(1)
    waiting = [0]  # tries queued for `checking`
    used_states = UsedStates(clock=clock)
    prefix = "__Host-" if settings.secure_cookies else ""
    admin_cookie = f"{prefix}dmbot_admin"
    signin_cookie = f"{prefix}dmbot_admin_signin"
    admin_page = f"{settings.site_url}/admin"

    def require_on() -> None:
        if not settings.admin_emails:
            raise HTTPException(status_code=404, detail="not_found")

    def set_admin_cookie(response: Response, token: str) -> None:
        # Strict: the browser never sends it on a request another site starts.
        response.set_cookie(
            admin_cookie,
            token,
            max_age=MAX_SECONDS,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="strict",
        )

    def current(request: Request) -> AdminSession:
        require_on()
        session = admin_sessions.find(request.cookies.get(admin_cookie))
        if session is None:
            raise HTTPException(status_code=401, detail="signed_out")
        return session

    def require_admin(request: Request) -> AdminSession:
        """For every admin POST: signed in, and the session's CSRF token sent."""
        session = current(request)
        if not csrf_ok(session, request.headers.get(CSRF_HEADER)):
            raise HTTPException(status_code=403, detail="not_allowed")
        return session

    Admin = Annotated[AdminSession, Depends(current)]
    AdminWrite = Annotated[AdminSession, Depends(require_admin)]

    async def read_json(request: Request) -> Any:
        """The body as JSON, at most MAX_BODY bytes; None if it isn't JSON."""
        with contextlib.suppress(ValueError):
            if int(request.headers.get("content-length", "0")) > MAX_BODY:
                raise HTTPException(status_code=413, detail="too_long")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise HTTPException(status_code=413, detail="too_long")
        try:
            return json.loads(body)
        except (ValueError, RecursionError):
            return None

    @api.get("/me")
    async def me(session: Admin) -> dict[str, str]:
        return {"email": session.email, "csrf": session.csrf}

    @api.get("/auth/ways")
    async def ways() -> dict[str, bool]:
        """Which sign-ins are set up, so the page shows only the ones that work."""
        require_on()
        return {"google": google is not None, "password": bool(settings.admin_password_hash)}

    @api.post("/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        require_on()
        token = request.cookies.get(admin_cookie)
        # A session that already ended has nothing to protect: just clear its old cookie.
        # A live one needs its CSRF token, like every admin POST.
        if admin_sessions.find(token) is not None:
            require_admin(request)
        admin_sessions.end(token)
        response = Response(status_code=204)
        response.delete_cookie(admin_cookie, path="/", secure=settings.secure_cookies)
        return response

    @api.post("/auth/password", status_code=204)
    async def password(request: Request) -> Response:
        require_on()
        raw = await read_json(request)
        email = raw.get("email") if isinstance(raw, dict) else None
        given = raw.get("password") if isinstance(raw, dict) else None
        if not isinstance(email, str) or not isinstance(given, str) or len(given) > 1024:
            raise HTTPException(status_code=400, detail="bad_request")
        try:
            # JSON can carry a lone surrogate ("\ud800"), which argon2 can't encode: refuse
            # it here, not as a 500 that skips the try count.
            email.encode("utf-8")
            given.encode("utf-8")
        except UnicodeEncodeError:
            raise HTTPException(status_code=400, detail="bad_request") from None
        email_key = "email:" + email.strip().lower()[:320]
        address = client_address(request)
        addr_key = "addr:" + rate_key(address)
        # One check at a time, so tries racing each other can't all pass the lock before
        # any failure counts, and a flood waits its turn instead of using every core. A
        # long queue means a flood: refuse at once, so the admin's own try isn't stuck
        # behind it until the page gives up.
        if waiting[0] >= MAX_WAITING:
            log.warning("Admin sign-in refused from %s: too many at once", address)
            raise HTTPException(status_code=503, detail="busy")
        waiting[0] += 1
        try:
            await checking.acquire()
        finally:
            waiting[0] -= 1
        try:
            # A locked try skips the hash: whether a key is locked is no secret (the page
            # says so), and hashing for it would let one client keep the API busy.
            if tries.locked(email_key) or tries.locked(addr_key):
                log.warning("Admin sign-in refused from %s: locked", address)
                raise HTTPException(status_code=401, detail=WRONG)
            # The same hash work for a wrong email as for a wrong password. In a thread:
            # argon2 takes a tenth of a second, which would stall every other request.
            right = await asyncio.to_thread(password_matches, settings.admin_password_hash, given)
            if not right or not email_allowed(email, settings.admin_emails):
                tries.fail(email_key)
                tries.fail(addr_key)
                log.warning("Admin sign-in refused from %s", address)
                raise HTTPException(status_code=401, detail=WRONG)
            tries.clear(email_key)
            tries.clear(addr_key)
        finally:
            checking.release()
        log.info("Admin signed in with the password from %s", address)
        response = Response(status_code=204)
        admin_sessions.end(request.cookies.get(admin_cookie))  # one session per browser
        set_admin_cookie(response, admin_sessions.start(email.strip().lower()))
        return response

    @api.get("/auth/google/start")
    async def google_start(request: Request) -> Response:
        require_on()
        if google is None:
            # A plain link leads here: answer with the page and its words, not bare JSON.
            return RedirectResponse(f"{admin_page}?signin=off", status_code=302)
        address = client_address(request)
        if tries.locked("addr:" + rate_key(address)):
            log.warning("Admin Google sign-in refused from %s: locked", address)
            return RedirectResponse(f"{admin_page}?signin=failed", status_code=302)
        # Nothing is kept on the server until the callback: a flood of starts costs nothing.
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        response = RedirectResponse(
            google.authorize_url(
                state=state,
                nonce=nonce,
                challenge=pkce_challenge(verifier),
                redirect_uri=settings.admin_google_redirect_uri,
            ),
            status_code=302,
        )
        # Lax, not Strict: Google's redirect back is a navigation another site starts.
        # Holds this sign-in's checks, signed, HttpOnly and short-lived, so the callback
        # must come back to the browser that started it; ':' as tokens can't hold dots.
        response.set_cookie(
            signin_cookie,
            tokens.make(
                settings.secret_key,
                "admin-google",
                f"{state}:{nonce}:{verifier}",
                now=clock(),
                seconds=SIGN_IN_SECONDS,
            ),
            max_age=SIGN_IN_SECONDS,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @api.get("/auth/google/callback")
    async def google_callback(request: Request) -> Response:
        require_on()
        failed = RedirectResponse(f"{admin_page}?signin=failed", status_code=302)
        failed.delete_cookie(signin_cookie, path="/", secure=settings.secure_cookies)
        if google is None:
            return failed
        held = tokens.read(
            settings.secret_key,
            "admin-google",
            request.cookies.get(signin_cookie, ""),
            now=clock(),
        )
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        address = client_address(request)
        addr_key = "addr:" + rate_key(address)
        parts = held.split(":") if held else []
        if (
            len(parts) != 3
            or not code
            or tries.locked(addr_key)
            or not secrets.compare_digest(parts[0].encode(), state.encode())
            or used_states.spent(parts[0])
        ):
            log.warning("Admin Google sign-in refused from %s: bad or expired check", address)
            return failed
        _state, nonce, verifier = parts
        # Counted as a wrong try before asking Google, cleared below if it works: a burst
        # of callbacks all passing the lock check before any failure counted would
        # otherwise each cost a call to Google.
        tries.fail(addr_key)
        try:
            claims = await google.claims(
                code=code, verifier=verifier, redirect_uri=settings.admin_google_redirect_uri
            )
        except AdminError as exc:
            log.warning("Admin Google sign-in failed: %s", exc)
            return failed
        email = verified_email(
            claims,
            client_id=settings.google_client_id,
            nonce=nonce,
            now=clock(),
            allowed=settings.admin_emails,
        )
        # Spent here, after Google vouched for an admin and with no await since: a
        # replay (even one racing this) finds it spent.
        if email is None:
            log.warning("Admin Google sign-in refused from %s: not an admin", address)
            return failed
        if not used_states.spend(parts[0]):
            log.warning("Admin Google sign-in refused from %s: too many recent sign-ins", address)
            return failed
        log.info("Admin signed in with Google from %s", address)
        done = RedirectResponse(admin_page, status_code=302)
        done.delete_cookie(signin_cookie, path="/", secure=settings.secure_cookies)
        tries.clear(addr_key)
        admin_sessions.end(request.cookies.get(admin_cookie))  # one session per browser
        set_admin_cookie(done, admin_sessions.start(email))
        return done

    # Free access (#773)

    @api.get("/grants")
    async def list_grants(session: Admin) -> dict[str, Any]:
        """The owner's free list (server settings, not editable here), the grants still in
        force or ended (revoked ones live in the history), and the last changes."""
        rows = await grants.grants(db)
        log_rows = await grants.log_entries(db, limit=RECENT_CHANGES)
        return {
            # Ids are strings: they don't fit in a JavaScript number.
            "free": sorted((str(i) for i in settings.free_users), key=int),
            "grants": [
                {
                    "discordId": str(r.discord_user_id),
                    "level": r.level,
                    "endsAt": r.ends_at,
                    "note": r.note,
                    "grantedBy": r.granted_by,
                    "grantedAt": r.granted_at,
                }
                for r in rows
                if r.revoked_at is None
            ],
            "log": [
                {
                    "at": e.at,
                    "by": e.admin_email,
                    "action": e.action,
                    "discordId": str(e.discord_user_id),
                }
                for e in log_rows
            ],
        }

    @api.post("/grants")
    async def give(request: Request, session: AdminWrite) -> dict[str, str]:
        raw = await read_json(request)
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="bad_request")
        discord_id, level = raw.get("discordId"), raw.get("level")
        ends_on, note = raw.get("endsOn"), raw.get("note", "")
        # Each refusal names what to fix; the site words it (web/src/content/admin.ts).
        if not _is_discord_id(discord_id):
            raise HTTPException(status_code=400, detail="bad_id")
        if int(discord_id) in settings.free_users:
            # Already covered by the server's list: a grant would only show them twice.
            raise HTTPException(status_code=400, detail="already_free")
        if level not in GRANT_LEVELS:
            raise HTTPException(status_code=400, detail="bad_level")
        if not isinstance(note, str) or not _storable(note):
            raise HTTPException(status_code=400, detail="bad_note")
        if len(" ".join(note.split())) > grants.NOTE_MAX:
            raise HTTPException(status_code=400, detail="long_note")
        ends_at = None
        if ends_on is not None:
            ends_at = _end_of_day(ends_on)
            if ends_at is None:
                raise HTTPException(status_code=400, detail="bad_date")
            if ends_at <= clock():
                raise HTTPException(status_code=400, detail="past_date")
        try:
            action = await grants.give(
                db, session.email, int(discord_id), level, ends_at=ends_at, note=note, now=clock()
            )
        except grants.GrantError as exc:
            raise HTTPException(status_code=400, detail="bad_request") from exc
        # The id only: never the note or the admin's email.
        log.info("Admin free access %s for %s", action, discord_id)
        return {"action": action}

    @api.post("/grants/{discord_id}/revoke", status_code=204)
    async def revoke(discord_id: str, session: AdminWrite) -> Response:
        if not _is_discord_id(discord_id):
            raise HTTPException(status_code=400, detail="bad_id")
        if not await grants.revoke(db, session.email, int(discord_id), now=clock()):
            # Never had one, already revoked, or on the server's free list (not a grant).
            raise HTTPException(status_code=404, detail="no_grant")
        log.info("Admin free access revoke for %s", discord_id)
        return Response(status_code=204)

    return api


def _is_discord_id(value: object) -> TypeGuard[str]:
    """17 to 20 digits that fit the database's BIGINT (Discord ids are 64-bit)."""
    return (
        isinstance(value, str)
        and DISCORD_ID.fullmatch(value) is not None
        and 0 < int(value) < 2**63
    )


def _storable(note: str) -> bool:
    """Postgres text holds no NUL, and a lone surrogate (JSON can carry one) can't be
    encoded: either would be a 500 instead of words."""
    if "\x00" in note:
        return False
    try:
        note.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _end_of_day(value: object) -> int | None:
    """An end date ("2026-12-31") as the moment access stops: the end of that day in UTC,
    so the person keeps it all that day. None if it isn't a date."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        day = datetime.date.fromisoformat(value)
        return calendar.timegm((day + datetime.timedelta(days=1)).timetuple())
    except (ValueError, OverflowError):  # 9999-12-31 has no next day
        return None
