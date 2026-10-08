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

import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from dmbot import entitlements, install, plans
from dmbot.db import Database
from dmbot.web import entitlements_writer, offers, sessions, tokens
from dmbot.web.accounts import (
    account_email,
    active_subscription,
    customer_id,
    delete_person,
    first_paid_month_after_trial,
    link_install,
    record_install,
)
from dmbot.web.discord import DiscordError, DiscordOAuth
from dmbot.web.me import build_me
from dmbot.web.payments import PaymentError, PaymentProvider, paid_plan_ids
from dmbot.web.sessions import Session
from dmbot.web.settings import WebSettings

log = logging.getLogger(__name__)

REQUEST_HEADER = "X-DMbot-Request"
FRESH_SIGN_IN_SECONDS = 24 * 3600
DELETE_SIGN_IN_SECONDS = 15 * 60
STATE_SECONDS = 600
MAX_WEBHOOK_BYTES = 64 * 1024
Clock = Callable[[], int]


def _system_clock() -> int:
    return int(time.time())


def create_app(
    settings: WebSettings,
    db: Database,
    discord: DiscordOAuth,
    *,
    payments: PaymentProvider | None = None,
    clock: Clock = _system_clock,
) -> FastAPI:
    """`payments` None: no payment company is set up yet; buying answers payments_off."""
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
    install_cookie = f"{prefix}dmbot_install"
    account_page = f"{settings.site_url}/account"

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # The payment company's webhook is checked by its signature instead (it can't send
        # our header or come from the website).
        webhook = request.url.path.startswith("/webhooks/")
        if request.method not in ("GET", "HEAD", "OPTIONS") and not webhook:
            origin = request.headers.get("origin")
            if request.headers.get(REQUEST_HEADER) != "1" or (
                origin is not None and origin != settings.site_origin
            ):
                return JSONResponse({"error": "not_allowed"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            # Any crash is still JSON the website can read, inside the CORS wrapper below
            # (a FastAPI Exception handler runs outside it). The log has the path, no data.
            log.exception("Request failed: %s %s", request.method, request.url.path)
            response = JSONResponse({"error": "server"}, status_code=500)
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

    # Adding DMbot to a server, and the account

    def fresh(session: Session, seconds: int = FRESH_SIGN_IN_SECONDS) -> None:
        """Acting for a server needs a recent sign-in (the list of servers someone manages
        is as old as their session); deleting the account, a very recent one."""
        if clock() - session.created_at > seconds:
            raise HTTPException(status_code=403, detail="sign_in_again")

    def managed(session: Session, server_id: str) -> int:
        try:
            guild_id = int(server_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found") from None
        if session.managed(guild_id) is None:
            raise HTTPException(status_code=403, detail="not_allowed")
        return guild_id

    @app.get("/install")
    async def install_start(request: Request, server_id: str = "") -> Response:
        """Sends the browser to Discord to add DMbot to one server the person manages.
        A plain link (GET): the website navigates here, so every answer is a redirect
        back to the account page, never raw JSON."""
        try:
            session, _token = await current_session(request)
            fresh(session)
            guild_id = managed(session, server_id)
        except HTTPException as exc:
            return RedirectResponse(f"{account_page}?install={exc.detail}", status_code=302)
        state = tokens.make(
            settings.secret_key,
            "install",
            f"{session.user_id}-{guild_id}",
            now=clock(),
            seconds=STATE_SECONDS,
        )
        response = RedirectResponse(
            discord.install_url(
                state, settings.install_redirect_uri, guild_id, install.permissions().value
            ),
            status_code=302,
        )
        response.set_cookie(
            install_cookie,
            state,
            max_age=STATE_SECONDS,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/install/callback")
    async def install_finish(request: Request) -> Response:
        def back(result: str) -> RedirectResponse:
            response = RedirectResponse(f"{account_page}?install={result}", status_code=302)
            response.delete_cookie(install_cookie, path="/", secure=settings.secure_cookies)
            return response

        failed = back("failed")
        try:
            session, _token = await current_session(request)
        except HTTPException:
            return back("signed_out")
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        cookie = request.cookies.get(install_cookie) or ""
        data = tokens.read(settings.secret_key, "install", state, now=clock())
        if not code or data is None or not hmac.compare_digest(state.encode(), cookie.encode()):
            return failed
        try:
            user_raw, guild_raw = data.split("-")
            if int(user_raw) != session.user_id:
                return failed
            guild_id = int(guild_raw)
        except ValueError:
            return failed
        token: str | None = None
        try:
            # The server comes from Discord's answer to the code, never from the address
            # (anyone can type a guild_id into a link), and the person from Discord too:
            # whoever approved on Discord must be the person signed in here.
            token, added_to = await discord.exchange_install(code, settings.install_redirect_uri)
            approver = await discord.user(token)
            if added_to != guild_id:
                return failed
            if approver.id != session.user_id:
                # Discord was signed in as someone else: DMbot joined, but not as theirs.
                return back("other_account")
            result = await record_install(
                db, session.user_id, guild_id, now=clock(), session=session.id_hash
            )
        except DiscordError as exc:
            log.warning("Install failed: %s", exc)
            return failed
        except Exception:
            log.exception("Install failed unexpectedly")
            return failed
        finally:
            if token is not None:
                try:
                    await discord.revoke(token)
                except DiscordError as exc:
                    log.warning("Couldn't revoke an install token: %s", exc)
        log.info("DMbot installed on server %s by user %s: %s", guild_id, session.user_id, result)
        return back("done" if result == "recorded" else result)

    @app.post("/servers/{server_id}/link", status_code=204)
    async def link_server(server_id: str, signed: Signed) -> Response:
        """Records that this person added DMbot to a server it joined through a plain link."""
        session, _token = signed
        fresh(session)
        guild_id = managed(session, server_id)
        result = await link_install(db, session.user_id, guild_id, session=session.id_hash)
        if result != "linked":
            raise HTTPException(status_code=409, detail=result)
        return Response(status_code=204)

    def delete_binding(session: Session, cookie_token: str) -> str:
        # The confirmation belongs to this one session: a copy can't delete an account
        # made later, or be used from another browser.
        return f"{session.user_id}-{sessions.hash_token(cookie_token)[:32]}"

    @app.post("/account/delete/request")
    async def delete_request(signed: Signed) -> dict[str, str]:
        """Step 1 of deleting the account: a 10-minute confirmation for step 2."""
        session, cookie_token = signed
        fresh(session, DELETE_SIGN_IN_SECONDS)
        confirm = tokens.make(
            settings.secret_key,
            "delete",
            delete_binding(session, cookie_token),
            now=clock(),
            seconds=STATE_SECONDS,
        )
        return {"confirm_token": confirm}

    @app.post("/account/delete/confirm")
    async def delete_confirm(signed: Signed, request: Request) -> Response:
        """Step 2: with step 1's confirmation, stop payments, then delete the account."""
        session, cookie_token = signed
        fresh(session, DELETE_SIGN_IN_SECONDS)
        try:
            body = await request.json()
        except ValueError:
            body = None
        given = body.get("confirm_token") if isinstance(body, dict) else None
        if not isinstance(given, str) or tokens.read(
            settings.secret_key, "delete", given, now=clock()
        ) != delete_binding(session, cookie_token):
            raise HTTPException(status_code=403, detail="confirm_again")
        # Stop the payments first: a deleted account must never be charged again. The
        # subscription ends with the month already paid for (no refund, no new charge).
        subscription = await active_subscription(db, session.user_id)
        if subscription is not None:
            if payments is None or subscription[0] != payments.name:
                raise HTTPException(status_code=503, detail="payments_unavailable")
            try:
                await payments.cancel(subscription_id=subscription[1])
            except PaymentError as exc:
                log.warning("Couldn't stop a subscription for a deletion: %s", exc)
                raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        await delete_person(db, session.user_id)
        log.info("Account deleted: user %s", session.user_id)
        response = Response(status_code=204)
        response.delete_cookie(session_cookie, path="/", secure=settings.secure_cookies)
        return response

    # Plans and payments

    @app.post("/plan/try-it", status_code=204)
    async def try_it(signed: Signed) -> Response:
        session, _token = signed
        result = await entitlements_writer.start_try_it(db, session.user_id, now=clock())
        if not result.started:
            # "try_it_used": only once per Discord user; "has_plan": a plan already works.
            raise HTTPException(status_code=409, detail=f"try_it_{result.reason}")
        log.info("Try It started: user %s", session.user_id)
        return Response(status_code=204)

    # Hand-over offers (#614): answered with the same store code as the bot's buttons.

    async def answer_offer(signed: tuple[Session, str], ref: str, what: offers.Answer) -> Response:
        session, _token = signed
        outcome = await offers.answer(db, session, ref, what, now=clock())
        if outcome == "no_free_slot":
            raise HTTPException(status_code=409, detail="no_free_slot")
        if outcome == "gone":
            # Answered, withdrawn, expired, or never this person's: the page reloads.
            raise HTTPException(status_code=409, detail="offer_gone")
        log.info("Hand-over offer %s %s by user %s", ref, outcome, session.user_id)
        return Response(status_code=204)

    @app.post("/offers/{ref}/accept", status_code=204)
    async def accept_offer(ref: str, signed: Signed) -> Response:
        return await answer_offer(signed, ref, "accept")

    @app.post("/offers/{ref}/decline", status_code=204)
    async def decline_offer(ref: str, signed: Signed) -> Response:
        return await answer_offer(signed, ref, "decline")

    @app.post("/offers/{ref}/withdraw", status_code=204)
    async def withdraw_offer(ref: str, signed: Signed) -> Response:
        return await answer_offer(signed, ref, "withdraw")

    def require_payments() -> PaymentProvider:
        if payments is None:
            raise HTTPException(status_code=503, detail="payments_off")
        return payments

    @app.post("/billing/checkout")
    async def checkout(signed: Signed, request: Request) -> dict[str, str]:
        provider = require_payments()
        session, _token = signed
        try:
            body = await request.json()
            plan_id = str(body["plan"]) if isinstance(body, dict) else ""
        except (ValueError, KeyError):
            plan_id = ""
        # The price comes from plans.json by id, never from the request.
        if plan_id not in paid_plan_ids():
            raise HTTPException(status_code=400, detail="unknown_plan")
        plan = plans.load().by_id[plan_id]
        current = await entitlements.get(db, session.user_id)
        if current is not None and current.plan != "try-it" and current.status != "lapsed":
            # A paid plan is working: change it on the billing page, never a second
            # subscription (or a second first-month offer).
            raise HTTPException(status_code=409, detail="has_paid_plan")
        first_month: int | None = None
        if plan.first_month_after_trial_cents is not None and await first_paid_month_after_trial(
            db, session.user_id
        ):
            first_month = plan.first_month_after_trial_cents
        email = await account_email(db, session.user_id)
        try:
            url = await provider.checkout_url(
                user_id=session.user_id,
                email=email,
                plan=plan,
                first_month_cents=first_month,
                return_url=account_page,
            )
        except PaymentError as exc:
            log.warning("Checkout failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/billing/portal")
    async def billing_portal(signed: Signed) -> dict[str, str]:
        provider = require_payments()
        session, _token = signed
        plan = await entitlements.get(db, session.user_id)
        customer = await customer_id(db, session.user_id)
        if plan is None or customer is None:
            raise HTTPException(status_code=409, detail="no_paid_plan")
        try:
            url = await provider.billing_url(customer_id=customer, return_url=account_page)
        except PaymentError as exc:
            log.warning("Billing page failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/webhooks/{provider_name}")
    async def webhook(provider_name: str, request: Request) -> Response:
        """The payment company's events: signature checked, each applied once."""
        if payments is None or provider_name != payments.name:
            raise HTTPException(status_code=404, detail="not_found")
        # Read at most 64 KB, whatever Content-Length says (or doesn't).
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_WEBHOOK_BYTES:
                raise HTTPException(status_code=413, detail="too_large")
        if not payments.verify(body, request.headers):
            log.warning("Payment webhook with a bad signature refused")
            raise HTTPException(status_code=400, detail="bad_signature")
        try:
            event = payments.parse(body)
        except (ValueError, KeyError, TypeError, AttributeError):
            log.error("A signed payment event couldn't be read: needs a person to look at it")
            raise HTTPException(status_code=400, detail="bad_event") from None
        if event is None:
            return Response(status_code=200)  # an event DMbot doesn't use
        outcome = await entitlements_writer.apply_event(db, event, now=clock())
        log.info("Payment event %s for user %s: %s", event.event_id, event.user_id, outcome)
        if outcome == "retry":
            # Too early (its "started" hasn't arrived): the company delivers it again later.
            raise HTTPException(status_code=503, detail="retry_later")
        if outcome == "rejected":
            raise HTTPException(status_code=422, detail="bad_event")
        return Response(status_code=200)

    return app
