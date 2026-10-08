"""The admin page's sign-in routes (#772): Google, email and password, who is signed in,
and sign out. The rules are in dmbot.web.admin; part 3 (#773) adds the grants routes
behind `require_admin`.

Every POST already needs the website's own header and origin (the guard in dmbot.web.app);
admin POSTs after sign-in also need the session's CSRF token.
"""

# No `from __future__ import annotations` here: FastAPI must see the session types inside
# router() as real objects, or it reads them as query parameters.

import json
import logging
import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from dmbot.web import tokens
from dmbot.web.admin import (
    CSRF_HEADER,
    MAX_SECONDS,
    AdminError,
    AdminSession,
    AdminSessions,
    FailedTries,
    GoogleSignIn,
    csrf_ok,
    email_allowed,
    password_matches,
    pkce_challenge,
    verified_email,
)
from dmbot.web.settings import WebSettings

log = logging.getLogger(__name__)

SIGN_IN_SECONDS = 600
MAX_BODY = 4 * 1024
# The same answer for a wrong email, a wrong password and a locked try (#772).
WRONG = "wrong_sign_in"


def router(
    settings: WebSettings,
    *,
    google: GoogleSignIn | None,
    admin_sessions: AdminSessions,
    tries: FailedTries,
    client_address: Callable[[Request], str],
    clock: Callable[[], int],
) -> APIRouter:
    api = APIRouter(prefix="/admin")
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
    AdminPost = Annotated[AdminSession, Depends(require_admin)]

    @api.get("/me")
    async def me(session: Admin) -> dict[str, str]:
        return {"email": session.email, "csrf": session.csrf}

    @api.post("/auth/logout", status_code=204)
    async def logout(_session: AdminPost, request: Request) -> Response:
        admin_sessions.end(request.cookies.get(admin_cookie))
        response = Response(status_code=204)
        response.delete_cookie(admin_cookie, path="/", secure=settings.secure_cookies)
        return response

    @api.post("/auth/password", status_code=204)
    async def password(request: Request) -> Response:
        require_on()
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise HTTPException(status_code=413, detail="too_long")
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):
            raw = None
        email = raw.get("email") if isinstance(raw, dict) else None
        given = raw.get("password") if isinstance(raw, dict) else None
        if not isinstance(email, str) or not isinstance(given, str) or len(given) > 1024:
            raise HTTPException(status_code=400, detail="bad_request")
        email_key = "email:" + email.strip().lower()[:320]
        address = client_address(request)
        addr_key = "addr:" + address
        locked = tries.locked(email_key) or tries.locked(addr_key)
        # Always check the password, so a locked or unknown email takes as long as a real try.
        right = password_matches(settings.admin_password_hash, given)
        if locked or not right or not email_allowed(email, settings.admin_emails):
            if not locked:
                tries.fail(email_key)
                tries.fail(addr_key)
            log.warning("Admin sign-in refused from %s", address)
            raise HTTPException(status_code=401, detail=WRONG)
        tries.clear(email_key)
        tries.clear(addr_key)
        log.info("Admin signed in with the password from %s", address)
        response = Response(status_code=204)
        set_admin_cookie(response, admin_sessions.start(email.strip().lower()))
        return response

    @api.get("/auth/google/start")
    async def google_start() -> Response:
        require_on()
        if google is None:
            # A plain link leads here: answer with the page and its words, not bare JSON.
            return RedirectResponse(f"{admin_page}?signin=off", status_code=302)
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
        # Holds this sign-in's checks, signed and short-lived; ':' as tokens can't hold dots.
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
        parts = held.split(":") if held else []
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        address = client_address(request)
        if (
            len(parts) != 3
            or not code
            or tries.locked("addr:" + address)
            or not secrets.compare_digest(parts[0].encode(), state.encode())
        ):
            log.warning("Admin Google sign-in refused from %s: bad or expired check", address)
            return failed
        _state, nonce, verifier = parts
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
        if email is None:
            tries.fail("addr:" + address)
            log.warning("Admin Google sign-in refused from %s: not an admin", address)
            return failed
        log.info("Admin signed in with Google from %s", address)
        done = RedirectResponse(admin_page, status_code=302)
        done.delete_cookie(signin_cookie, path="/", secure=settings.secure_cookies)
        set_admin_cookie(done, admin_sessions.start(email))
        return done

    return api
