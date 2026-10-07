"""The website's API (#435): Discord sign-in, sessions and the account page's data.

Security rules (CLAUDE.md, #434, #435):
- Sign-in is Discord OAuth2 with a signed, browser-bound `state`. The Discord token is
  used once and revoked; it is never stored, logged, or put in a URL or cookie.
- The session cookie is httpOnly, Secure (on https), SameSite=Lax, and holds only a
  random token whose hash is stored.
- Every change (POST) needs the `X-DMbot-Request: 1` header, which a plain cross-site
  form can't send, and comes only from the website's own origin (CORS, exact origin).
- Logs carry ids only, never names, emails or tokens.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from dmbot.db import Database
from dmbot.web import sessions
from dmbot.web.discord import DiscordError, DiscordOAuth
from dmbot.web.me import build_me
from dmbot.web.sessions import Session
from dmbot.web.settings import WebSettings

log = logging.getLogger(__name__)

REQUEST_HEADER = "X-DMbot-Request"
Clock = Callable[[], int]


def _system_clock() -> int:
    return int(time.time())


def create_app(
    settings: WebSettings,
    db: Database,
    discord: DiscordOAuth,
    *,
    clock: Clock = _system_clock,
) -> FastAPI:
    app = FastAPI(
        title="DMbot web API",
        docs_url=None,  # no public API browser
        redoc_url=None,
        openapi_url=None,
    )
    # The __Host- prefix makes browsers refuse a cookie unless it's Secure, set by this
    # exact host with Path=/ and no Domain: no other site or subdomain (the website shares
    # this API's site) can plant or read it. Plain names only for http://localhost testing.
    prefix = "__Host-" if settings.secure_cookies else ""
    session_cookie = f"{prefix}dmbot_session"
    state_cookie = f"{prefix}dmbot_signin"
    account_page = f"{settings.site_url}/account"

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get(REQUEST_HEADER) != "1" or (
                origin is not None and origin != settings.site_origin
            ):
                return JSONResponse({"error": "not_allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Added after the guard, so it wraps it: refusals and errors carry CORS headers too,
    # and the website can read them as "not allowed" or "server", not as "network".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.site_origin],  # exactly the website, never echoed back
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[REQUEST_HEADER, "Content-Type"],
        max_age=600,
    )

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            session_cookie,
            token,
            max_age=settings.session_days * 86400,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )

    async def current_session(request: Request) -> tuple[Session, str]:
        token = request.cookies.get(session_cookie)
        if not token:
            raise HTTPException(status_code=401, detail="signed_out")
        found = await sessions.find(db, token, now=clock())
        if found is None:
            raise HTTPException(status_code=401, detail="signed_out")
        return found, token

    Signed = Annotated[tuple[Session, str], Depends(current_session)]

    @app.exception_handler(HTTPException)
    async def plain_errors(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/discord/start")
    async def sign_in_start() -> Response:
        state = sessions.new_state(settings.secret_key, clock())
        response = RedirectResponse(
            discord.authorize_url(state, settings.oauth_redirect_uri), status_code=302
        )
        response.set_cookie(
            state_cookie,
            state,
            max_age=sessions.STATE_SECONDS,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/auth/discord/callback")
    async def sign_in_finish(request: Request) -> Response:
        # Read by hand (not as typed parameters) so nothing malformed becomes an error page.
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        failed = RedirectResponse(f"{account_page}?signin=failed", status_code=302)
        failed.delete_cookie(state_cookie, path="/", secure=settings.secure_cookies)
        cookie = request.cookies.get(state_cookie)
        if not code or not sessions.state_ok(settings.secret_key, state, cookie, clock()):
            return failed
        token: str | None = None
        try:
            token = await discord.exchange(code, settings.oauth_redirect_uri)
            user = await discord.user(token)
            guilds = await discord.guilds(token)
            session_token = await sessions.sign_in(
                db, user, guilds, now=clock(), days=settings.session_days
            )
        except DiscordError as exc:
            log.warning("Sign-in failed: %s", exc)
            return failed
        except Exception:
            # A bad answer from Discord or a database problem: the person gets the plain
            # "try again" page, the log gets the details (no token, no names).
            log.exception("Sign-in failed unexpectedly")
            return failed
        finally:
            if token is not None:
                try:
                    await discord.revoke(token)
                except DiscordError as exc:
                    log.warning("Couldn't revoke a Discord sign-in token: %s", exc)
        log.info("Signed in: user %s", user.id)
        response = RedirectResponse(account_page, status_code=302)
        response.delete_cookie(state_cookie, path="/", secure=settings.secure_cookies)
        set_session_cookie(response, session_token)
        return response

    @app.post("/auth/logout", status_code=204)
    async def sign_out(request: Request) -> Response:
        """Always ends with the person signed out, even if the session had already gone."""
        token = request.cookies.get(session_cookie)
        if token:
            found = await sessions.find(db, token, now=clock())
            if found is not None:
                await sessions.sign_out(db, found.user_id, token)
        response = Response(status_code=204)
        response.delete_cookie(session_cookie, path="/", secure=settings.secure_cookies)
        return response

    @app.get("/me")
    async def me(signed: Signed) -> dict[str, Any]:
        session, _token = signed
        return await build_me(db, session, now=clock())

    return app
