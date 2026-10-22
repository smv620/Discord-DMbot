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

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import ipaddress
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from dmbot import consent_notify, entitlements, install, mail, plans
from dmbot.account_id import from_dmbot_id, to_dmbot_id
from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.link_tokens import check_link_token, check_unsubscribe_token
from dmbot.logs import mask_tokens
from dmbot.recaps import voice
from dmbot.web import (
    admin_api,
    all_names,
    email_change,
    email_signin,
    entitlements_writer,
    feedback,
    imports,
    npc_pages,
    offers,
    sessions,
    shared_recaps,
    strays,
    support,
    tokens,
)
from dmbot.web import homebrew as homebrew_pages
from dmbot.web import house_rules as house_rule_pages
from dmbot.web import recap_audio as audio_links
from dmbot.web import transcripts as transcript_pages
from dmbot.web.accounts import (
    account_email,
    active_subscription,
    customer_id,
    delete_person,
    dmbot_id,
    link_install,
    record_install,
    user_of_dmbot_id,
)
from dmbot.web.admin import CSRF_HEADER, AdminSessions, FailedTries, GoogleSignIn
from dmbot.web.characters import build_characters
from dmbot.web.discord import DiscordError, DiscordOAuth
from dmbot.web.export import build_export
from dmbot.web.feedback import Discussions, FeedbackError, HumanCheck
from dmbot.web.improvement import read_choice as read_improvement
from dmbot.web.improvement import save_choice as save_improvement
from dmbot.web.limits import LIMITS, RateLimit, free_ticket_refusal
from dmbot.web.me import build_me
from dmbot.web.moving import MoveCodes, can_move
from dmbot.web.name_checks import answer as answer_name_check
from dmbot.web.name_checks import read_checks
from dmbot.web.names import list_names, set_said_as
from dmbot.web.payments import (
    NotResumable,
    PaymentError,
    PaymentProvider,
    ResumeRefused,
    paid_plan_ids,
)
from dmbot.web.recap_choices import read_choices, set_choice
from dmbot.web.recap_requests import (
    STATUS,
    read_instructions,
    read_voice,
    rewrite,
    send_instructions,
    send_voice,
)
from dmbot.web.recaps import (
    RecapError,
    approve_recap,
    audio_again,
    decide,
    edit_recap,
    list_recaps,
    month_usage,
    read_recap,
    share_recap,
)
from dmbot.web.return_path import safe_return_path
from dmbot.web.sessions import Session
from dmbot.web.settings import WebSettings
from dmbot.web.support_ai import SupportHelper
from dmbot.web.support_mail import (
    MAX_BODY_BYTES,
    fresh_words,
    recipients,
    sender_address,
    ticket_of,
    verify_signature,
)

log = logging.getLogger(__name__)

REQUEST_HEADER = "X-DMbot-Request"
FRESH_SIGN_IN_SECONDS = 24 * 3600
DELETE_SIGN_IN_SECONDS = 15 * 60
STATE_SECONDS = 600
MAX_WEBHOOK_BYTES = 64 * 1024
# A feedback form: 2,000 characters (up to 4 bytes each, more once JSON-escaped), a
# contact and a Turnstile token.
MAX_FEEDBACK_BYTES = 32 * 1024
# A support ticket: 4,000 characters plus a subject and a Turnstile token (the same slack).
MAX_SUPPORT_BYTES = 48 * 1024
Clock = Callable[[], int]


class _Refused(Exception):
    """An answer to give that keeps the turn used (see send_feedback)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _system_clock() -> int:
    return int(time.time())


def create_app(
    settings: WebSettings,
    db: Database,
    discord: DiscordOAuth,
    *,
    payments: PaymentProvider | None = None,
    discussions: Discussions | None = None,
    human_check: HumanCheck | None = None,
    feedback_limit: RateLimit | None = None,
    support_limit: RateLimit | None = None,
    export_limit: RateLimit | None = None,
    email_limit: RateLimit | None = None,
    mailer: mail.Mailer | None = None,
    move_codes: MoveCodes | None = None,
    route_limits: dict[str, RateLimit] | None = None,
    google: GoogleSignIn | None = None,
    helper: SupportHelper | None = None,
    admin_sessions: AdminSessions | None = None,
    admin_tries: FailedTries | None = None,
    clock: Clock = _system_clock,
) -> FastAPI:
    """`payments` None: no payment company is set up yet; buying answers payments_off.
    `discussions` None: the "Say hello" forms answer feedback_off. `human_check` None:
    no Turnstile check (settings allow that only on localhost)."""
    if feedback_limit is None:
        feedback_limit = RateLimit()
    if support_limit is None:
        # Support tickets (#1340): one per account per 10 minutes, counted by account (the
        # person is signed in), so a shared address can't hold one person's turn.
        support_limit = RateLimit()
    if email_limit is None:
        # Sign-in links (#1231): three a hour per email address, kept apart from the per-connection
        # limit so one person can't fill someone else's inbox from many connections. Room for a
        # lot of addresses at once: a full limiter lets links through (see the route), but a
        # flood of made-up addresses shouldn't fill it in the first place.
        email_limit = RateLimit(60 * 60, count=3, most=500_000)
    if export_limit is None:
        # The data export (#1150): once a minute per person, counted by account, not address.
        export_limit = RateLimit(60)
    # One limiter per group of routes in limits.LIMITS (#1082); tests pass fake clocks.
    if route_limits is None:
        route_limits = {
            name: RateLimit(group.seconds, count=group.count) for name, group in LIMITS.items()
        }
    limited_paths = {path: name for name, group in LIMITS.items() for path in group.paths}
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
    # The page a signed-out person asked for (#2301), kept just long enough to come back to it.
    return_cookie = f"{prefix}dmbot_signin_next"
    account_page = f"{settings.site_url}/account"

    def limit_key(address: str) -> str:
        """One person can hold a whole IPv6 /64, so count by that block; an IPv4 address,
        or "unknown", is its own key."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return address
        if ip.version == 6:
            return str(ipaddress.ip_network(f"{ip}/64", strict=False))
        return address

    def self_limit(request: Request) -> Response | None:
        """The API's own per-address limit on sign-in, install, billing and account
        deletion (#1082). The webhook is never limited here: its signature is the gate.
        A browser visit goes back to the site with a plain message; a call from the page
        gets a 429. The refusal says nothing about why beyond "too many tries"."""
        name = limited_paths.get(request.url.path)
        if name is None or request.method not in ("GET", "POST"):
            return None
        limit = route_limits[name]
        address = limit_key(client_address(request))
        verdict = limit.take(address)
        if verdict == "ok":
            return None
        wait = {"Retry-After": str(limit.retry_after(address) if verdict == "wait" else 60)}
        param = LIMITS[name].redirect_param
        if param is not None:
            return RedirectResponse(f"{account_page}?{param}=slow", status_code=302, headers=wait)
        if verdict == "busy":
            return JSONResponse({"error": "busy"}, status_code=503, headers=wait)
        return JSONResponse({"error": "slow_down"}, status_code=429, headers=wait)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # The payment company's webhook and the mail service's inbound one are checked by their
        # signatures instead (they can't send our header or come from the website).
        webhook = request.url.path.startswith("/webhooks/") or request.url.path == "/mail/inbound"
        if request.method not in ("GET", "HEAD", "OPTIONS") and not webhook:
            origin = request.headers.get("origin")
            if request.headers.get(REQUEST_HEADER) != "1" or (
                origin is not None and origin != settings.site_origin
            ):
                return JSONResponse({"error": "not_allowed"}, status_code=403)
        # After the cross-site check, so a forged POST from another site can't use up a
        # visitor's count. (A forged GET to a sign-in route still can; that is accepted:
        # the wait is minutes, and the proxy's rules are the first line.)
        response = self_limit(request)
        try:
            if response is None:
                response = await call_next(request)
        except Exception:
            # Any crash is still JSON the website can read, inside the CORS wrapper below
            # (a FastAPI Exception handler runs outside it). The log has the path, no data.
            log.exception("Request failed: %s %s", request.method, mask_tokens(request.url.path))
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
        allow_headers=[REQUEST_HEADER, CSRF_HEADER, "Content-Type"],
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
    async def sign_in_start(request: Request) -> Response:
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
        back = safe_return_path(request.query_params.get("next"))
        if back is not None:
            response.set_cookie(
                return_cookie,
                back,
                max_age=sessions.STATE_SECONDS,
                path="/",
                secure=settings.secure_cookies,
                httponly=True,
                samesite="lax",
            )
        else:
            response.delete_cookie(return_cookie, path="/", secure=settings.secure_cookies)
        return response

    @app.get("/auth/discord/callback")
    async def sign_in_finish(request: Request) -> Response:
        # Read by hand (not as typed parameters) so nothing malformed becomes an error page.
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        failed = RedirectResponse(f"{account_page}?signin=failed", status_code=302)
        failed.delete_cookie(state_cookie, path="/", secure=settings.secure_cookies)
        failed.delete_cookie(return_cookie, path="/", secure=settings.secure_cookies)
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
        except sessions.AccountMoved:
            # This Discord account's plan moved to another one (#1230): say so plainly.
            moved = RedirectResponse(f"{account_page}?signin=moved", status_code=302)
            moved.delete_cookie(state_cookie, path="/", secure=settings.secure_cookies)
            moved.delete_cookie(return_cookie, path="/", secure=settings.secure_cookies)
            return moved
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
        # Back to the page they asked for, checked again here: the cookie is only ever a hint, and
        # the address is always built on the site's own.
        back = safe_return_path(request.cookies.get(return_cookie))
        response = RedirectResponse(
            f"{settings.site_url}{back}" if back is not None else account_page, status_code=302
        )
        response.delete_cookie(state_cookie, path="/", secure=settings.secure_cookies)
        response.delete_cookie(return_cookie, path="/", secure=settings.secure_cookies)
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

    @app.post("/me/sign-out-everywhere", status_code=204)
    async def sign_out_everywhere(signed: Signed) -> Response:
        """End every sign-in of the signed-in person, this one too (#1163). Needs a sign-in
        (401 otherwise), so nobody ends anyone else's."""
        session, _token = signed
        await sessions.sign_out_everywhere(db, session.user_id)
        log.info("A person signed out everywhere")
        response = Response(status_code=204)
        response.delete_cookie(session_cookie, path="/", secure=settings.secure_cookies)
        return response

    async def small_json(request: Request) -> dict[str, Any]:
        """A small JSON object from the request, or {} for anything else (never an error page).
        Reads at most 2 KB, whatever the headers claim, so a long body costs no memory."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 2048:
                return {}
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):
            return {}
        return raw if isinstance(raw, dict) else {}

    email_tasks: set[asyncio.Task[None]] = set()
    app.state.email_tasks = email_tasks  # tests wait on these

    async def send_sign_in_link(email: str) -> None:
        """Look the address up, make the link and mail it: all after the answer has gone, so
        the answer takes the same time whether or not the address has an account."""
        if mailer is None:
            return  # nothing could be sent, so no link is made either
        try:
            made = await email_signin.make_link(db, email, now=clock())
            if made is not None:
                user_id, token = made
                # An account email (no unsubscribe): its kind has the link's own id, so each
                # link is its own email, and it counts toward the day's cap.
                result = await mailer.send(
                    user_id=user_id,
                    kind=f"sign-in:{email_signin.link_id(token)}",
                    to=email,
                    subject="Your DMbot sign-in link",
                    body=(
                        "Here is your DMbot sign-in link:\n\n"
                        f"{account_page}#email-link={token}\n\n"
                        "It works once and ends after 15 minutes. If you didn't ask for it, "
                        "you can ignore this email."
                    ),
                )
                if result != "sent":
                    log.warning("A sign-in link was not sent: %s", result)
        except Exception as exc:
            # The type only: mail libraries put the address in their messages.
            log.error("A sign-in link could not be made or sent: %s", type(exc).__name__)

    @app.post("/auth/email/request", status_code=202)
    async def email_sign_in_request(request: Request) -> dict[str, str]:
        """Email a one-use sign-in link if the address belongs to one account (#1231). The
        answer is the same words and the same status, and comes back at the same speed,
        whatever happens (no account, two accounts, too many asks, a mail error), so it never
        tells anyone whether an address has an account. Only a refusal for this connection's
        own count differs, and that says nothing about the address."""
        answer = {"message": "If that email belongs to a DMbot account, we've sent a link"}
        email = email_signin.clean_email((await small_json(request)).get("email"))
        if email is None:
            return answer
        key = hashlib.sha256(email.encode()).hexdigest()
        taken = email_limit.take(key)
        if taken == "busy":
            # So many different addresses at once that the limiter is full: links still go (the
            # per-connection limit is the brake), or one flood would stop everyone's.
            log.warning("Sign-in links are being asked for by very many different addresses")
        elif taken != "ok":
            return answer  # too many for this address: silently no email, same reply
        task = asyncio.create_task(send_sign_in_link(email))
        email_tasks.add(task)  # held until done, so it can't be collected half-way
        task.add_done_callback(email_tasks.discard)
        return answer

    @app.post("/auth/email/verify", status_code=204)
    async def email_sign_in_use(request: Request) -> Response:
        """Use an emailed link: the same session cookie a Discord sign-in makes. One use,
        15 minutes. The page posts the token (it came after a `#`, so no server or log saw it)."""
        token = (await small_json(request)).get("token")
        user_id = (
            await email_signin.redeem(db, token, now=clock()) if isinstance(token, str) else None
        )
        if user_id is None:
            raise HTTPException(status_code=400, detail="bad_link")
        try:
            session_token = await sessions.sign_in_by_email(
                db, user_id, now=clock(), days=settings.session_days
            )
        except LookupError:
            # The account went between the link being made and used.
            raise HTTPException(status_code=400, detail="bad_link") from None
        log.info("Signed in by email link: user %s", user_id)
        response = Response(status_code=204)
        set_session_cookie(response, session_token)
        return response

    @app.get("/personal")
    async def personal_page(uid: str = "", t: str = "") -> dict[str, Any]:
        """What a personal link (`?uid=&t=`) may show on /pricing and /hello: the Discord name
        and the experience points (#1231). Both need the signed code in `t`, so guessing an ID
        shows nothing. Every failure is the same 404, so it can't be used to find accounts."""
        try:
            user_id = from_dmbot_id(uid)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found") from None
        # With no LINK_TOKEN_SECRET set, personal links are off: nothing passes the check.
        if not check_link_token(uid, t, int(clock()), settings.link_token_secret):
            raise HTTPException(status_code=404, detail="not_found")
        async with db.user(user_id) as conn:
            cur = await conn.execute(
                "SELECT display_name FROM web_users WHERE user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
            cur = await conn.execute("SELECT dmbot_minutes_total() AS minutes")
            minutes = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        name = row["display_name"]
        return {
            "name": name or None,
            "experiencePoints": int(minutes["minutes"]) if minutes else 0,
        }

    @app.post("/me/move-code", status_code=202)
    async def move_code(request: Request, signed: Signed) -> dict[str, str]:
        """Email a move code to the account's payment email (#1231; the code itself is #1230's).
        Paid, active plans only, and never shown on screen: it only goes to the email."""
        session, _token = signed
        if move_codes is None:
            raise HTTPException(status_code=503, detail="move_off")
        plan, _grant, _access = await entitlements.plan_grant_access(db, session.user_id, clock())
        if not can_move(plan, clock()):
            raise HTTPException(status_code=403, detail="not_paid")
        # The database counts asks per account and per connection. The connection is a keyed
        # hash (HMAC with the server's secret): a plain hash of an IPv4 address reverses in
        # seconds, so the address could be read back from the table.
        source = hmac.new(
            settings.secret_key,
            f"move-code:{limit_key(client_address(request))}".encode(),
            hashlib.sha256,
        ).hexdigest()
        outcome = await move_codes.send_code(session.user_id, source)
        if outcome == "sent":
            log.info("A move code was sent for user %s", session.user_id)
            return {"message": "We've emailed a move code to your payment email."}
        # Plain words the page shows; none of them holds the code or the address.
        refusals = {
            "not_paid": (403, "not_paid"),
            "too_soon": (409, "too_soon"),
            "slow_down": (429, "slow_down"),
            "no_email": (409, "no_email"),
        }
        status, detail = refusals.get(outcome, (502, "email_failed"))
        raise HTTPException(status_code=status, detail=detail)

    @app.get("/me")
    async def me(signed: Signed) -> dict[str, Any]:
        session, _token = signed
        return await build_me(
            db,
            session,
            now=clock(),
            recap_audio_for_sale=payments is not None and payments.recap_audio_for_sale,
            imports_for=settings.imports_enabled_for,
        )

    change_limit = RateLimit(60 * 60, count=6)  # asks to change the email, counted by account

    async def mail_change_link(user_id: int, new: str, token: str) -> bool:
        """Mail the confirm link to the NEW address. False if it could not be sent."""
        if mailer is None:
            return False
        result = await mailer.send(
            user_id=user_id,
            kind=f"email-change:{email_change.link_id(token)}",
            to=new,
            subject="Confirm your new DMbot email",
            body=(
                "Someone asked to use this address for a DMbot account. To confirm, open this "
                f"link while you are signed in to that account:\n\n{account_page}"
                f"#email-confirm={token}\n\nIt works once and ends after 15 minutes. If you "
                "didn't ask for it, ignore this email and nothing changes."
            ),
        )
        if result != "sent":
            log.warning("An email-change link was not sent: %s", result)
        return result == "sent"

    @app.get("/me/email")
    async def me_email(signed: Signed) -> dict[str, Any]:
        """The address DMbot emails this person at, where it came from, and a change waiting
        for its link (#2349). Their own only."""
        session, _token = signed
        now = await email_change.read(db, session.user_id, now=int(clock()))
        return {"address": now.address, "source": now.source, "pending": now.pending}

    @app.put("/me/email")
    async def change_me_email(signed: Signed, request: Request) -> dict[str, Any]:
        """Ask to use another address: `{"address": "..."}`. A link goes to the new one and
        nothing changes until it is confirmed. A payment email is changed in Stripe."""
        session, _token = signed
        taken = change_limit.take(str(session.user_id))
        if taken != "ok":
            raise HTTPException(status_code=429, detail="slow_down" if taken == "wait" else "busy")
        made = await email_change.ask(
            db, session.user_id, (await small_json(request)).get("address"), now=int(clock())
        )
        if made is None:
            return {"pending": None}  # it is already the address in use
        if isinstance(made, str):
            status = {"bad_email": 400, "not_allowed": 403, "in_use": 409}[made]
            raise HTTPException(status_code=status, detail=made)
        new, token = made
        if not await mail_change_link(session.user_id, new, token):
            await email_change.cancel(db, session.user_id)
            raise HTTPException(status_code=502, detail="email_failed")
        log.info("An email change was asked for by user %s", session.user_id)
        return {"pending": new}

    @app.post("/me/email/confirm")
    async def confirm_me_email(signed: Signed, request: Request) -> dict[str, Any]:
        """The link's token (it came after a `#`, so no server saw it), posted by the signed-in
        account that asked: only that account can use it. One use, 15 minutes."""
        session, _token = signed
        done = await email_change.confirm(
            db, session.user_id, (await small_json(request)).get("token"), now=int(clock())
        )
        if done == "in_use":
            raise HTTPException(status_code=409, detail="in_use")
        if done is None:
            raise HTTPException(status_code=400, detail="bad_link")
        old, new = done
        if old and mailer is not None and old.lower() != new.lower():
            # Told to the OLD address: a stolen session cannot quietly move email sign-in.
            await mailer.send(
                user_id=session.user_id,
                kind=f"email-changed:{email_change.link_id(new + str(int(clock())))}",
                to=old,
                subject="Your DMbot email was changed",
                body=(
                    f"Your DMbot email was changed to {new}. If this wasn't you, sign in with "
                    f"Discord and sign out everywhere on your account page, then change it back: "
                    f"{account_page}"
                ),
            )
        log.info("An email change was confirmed by user %s", session.user_id)
        return {"address": new}

    @app.delete("/me/email/pending", status_code=204)
    async def cancel_me_email(signed: Signed) -> Response:
        session, _token = signed
        await email_change.cancel(db, session.user_id)
        return Response(status_code=204)

    @app.get("/me/improvement")
    async def me_improvement(signed: Signed) -> dict[str, Any]:
        """Whether DMbot may use the signed-in person's words to improve itself (#1609)."""
        session, _token = signed
        return await read_improvement(db, session)

    @app.put("/me/improvement")
    async def set_me_improvement(signed: Signed, request: Request) -> dict[str, Any]:
        """Change it: `{"improve": true | false}`. Nothing is deleted by turning it off."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        improve = body.get("improve") if isinstance(body, dict) else None
        if not isinstance(improve, bool):
            raise HTTPException(status_code=422, detail="bad_choice")
        saved = await save_improvement(db, session, improve)
        log.info("A website improvement choice was saved")
        return saved

    @app.get("/me/characters")
    async def me_characters(signed: Signed) -> dict[str, Any]:
        """The signed-in person's own character sheets, read-only (#1164)."""
        session, _token = signed
        return {"characters": await build_characters(db, session)}

    recap_refusals = {
        "not_found": (404, "not_found"),
        "not_allowed": (409, "not_allowed"),
        "not_editable": (409, "not_editable"),
        "bad_text": (422, "bad_text"),
        "too_soon": (429, "too_soon"),
        "bad_choice": (422, "bad_choice"),
        "too_big": (413, "too_big"),
        # Importing older sessions (#2105)
        "not_a_dm": (403, "not_a_dm"),
        "not_on_a_paid_plan": (402, "not_on_a_paid_plan"),
        "try_it_campaign": (402, "try_it_campaign"),
        "too_many_files": (413, "too_many_files"),
        "bad_files": (422, "bad_files"),
        "bad_mapping": (422, "bad_mapping"),
        "bad_date": (422, "bad_date"),
        "no_lines_kept": (422, "no_lines_kept"),
        "too_many_lines": (413, "too_many_lines"),
        "too_many_speakers": (413, "too_many_speakers"),
        "not_posted": (409, "not_posted"),  # only a posted recap has a public link (#2118)
        "bad_action": (422, "bad_action"),
        "not_in_plan": (409, "not_in_plan"),
        "audio_off": (409, "audio_off"),
        "names_secret": (409, "names_secret"),  # the text names a secret (#2083)
        "needs_approval": (409, "needs_approval"),  # the text changed since it was approved
        "stopped_recording": (409, "stopped_recording"),  # a player in it is no longer recorded
        "no_tries": (409, "no_tries"),  # the recap has had all its tries (#2113)
        "not_in_server": (403, "not_in_server"),  # an NPC page is for the server's people (#2265)
        "bad_page": (400, "bad_page"),  # a page past the end of the All names list (#2294)
        "bad_sort": (400, "bad_sort"),
    }
    recap_files = audio_links.audio_files(settings.recap_audio_dir)

    @app.get("/me/recaps")
    async def me_recaps(signed: Signed) -> dict[str, Any]:
        """The signed-in DM's latest session recaps (#1382). A player sees none."""
        session, _token = signed
        return {
            "recaps": await list_recaps(db, session),
            # Per campaign: the month's count against the allowance (#1428); free access and
            # grants are not limited by the bot, whatever is shown here.
            "recapsThisMonth": await month_usage(
                db,
                session,
                now=clock(),
                recaps_allowed=settings.recaps_per_month,
                audio_allowed=settings.recap_audio_per_month,
            ),
        }

    @app.get("/me/recaps/{ref}")
    async def me_recap(ref: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await read_recap(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.put("/me/recaps/{ref}")
    async def me_recap_edit(ref: str, signed: Signed, request: Request) -> Any:
        """Save the DM's edit of the text and the housekeeping (never logged)."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        summary = body.get("summary") if isinstance(body, dict) else None
        bullets = body.get("housekeeping") if isinstance(body, dict) else None
        if not isinstance(summary, str) or not (
            isinstance(bullets, list) and all(isinstance(b, str) for b in bullets)
        ):
            raise HTTPException(status_code=422, detail="bad_text")
        try:
            return await edit_recap(db, session, ref, summary=summary, housekeeping=bullets)
        except RecapError as err:
            if err.problems is not None:  # the cues are wrong: say where, and save nothing
                return JSONResponse(
                    {"error": "cue_problems", "cue_problems": err.problems}, status_code=422
                )
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    # What the DM types on the recap page (#2113): queued for the bot to sort (no AI key here),
    # polled by the page. The words are never logged.
    instructions_limit = RateLimit(60, count=12)
    name_answer_limit = RateLimit(60, count=60)  # a page of names answered one after another
    request_status = STATUS
    voice_types = voice.TYPES
    voice_max = voice.MAX_CLIP_BYTES

    async def bounded_json(request: Request, limit: int = 8 * 1024) -> Any:
        """A small JSON body, read with a running cap (a declared length is not trusted)."""
        length = request.headers.get("content-length", "")
        if length.isdecimal() and int(length) > limit:
            raise HTTPException(status_code=413, detail="too_big")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(status_code=413, detail="too_big")
        try:
            return json.loads(bytes(data))
        except ValueError:
            return None

    @app.post("/me/recaps/{ref}/instructions", status_code=202)
    async def me_recap_instructions(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        if instructions_limit.take(f"instructions:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        body = await bounded_json(request)
        try:
            return await send_instructions(
                db, session, ref, body.get("text") if isinstance(body, dict) else None
            )
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/recaps/{ref}/instructions/{request_id}")
    async def me_recap_instructions_state(
        ref: str, request_id: str, signed: Signed
    ) -> dict[str, Any]:
        session, _token = signed
        try:
            return await read_instructions(db, session, ref, request_id)
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    # A short spoken note (#2113): the clip is held in memory and in the request row only until the
    # bot has written it down. The web side has no speech key either.
    voice_limit = RateLimit(60, count=6)

    @app.post("/me/recaps/{ref}/voice", status_code=202)
    async def me_recap_voice(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        if voice_limit.take(f"voice:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind not in voice_types:
            raise HTTPException(status_code=415, detail="bad_type")
        length = request.headers.get("content-length", "")
        if length.isdecimal() and int(length) > voice_max:
            raise HTTPException(status_code=413, detail="too_big")
        data = bytearray()
        async for chunk in request.stream():  # a running cap: a declared length is not trusted
            data += chunk
            if len(data) > voice_max:
                raise HTTPException(status_code=413, detail="too_big")
        try:
            return await send_voice(
                db, session, ref, bytes(data), kind, outside=settings.outside_engine
            )
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/recaps/{ref}/voice/{request_id}")
    async def me_recap_voice_state(ref: str, request_id: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await read_voice(db, session, ref, request_id)
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.post("/me/recaps/{ref}/rewrite")
    async def me_recap_rewrite(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        if instructions_limit.take(f"rewrite:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        body = await bounded_json(request)
        try:
            return await rewrite(db, session, ref, body)
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    # The recap's names to check, answered on its page (#2113): the list, and one answer at a time,
    # queued for the bot to apply like Discord's name check. The DM's words are never logged.
    @app.get("/me/recaps/{ref}/names-to-check")
    async def me_recap_name_checks(ref: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await read_checks(db, session, ref)
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.post("/me/recaps/{ref}/names-to-check/{entity_id}", status_code=202)
    async def me_recap_name_answer(
        ref: str, entity_id: str, signed: Signed, request: Request
    ) -> dict[str, Any]:
        session, _token = signed
        if name_answer_limit.take(f"name-answer:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        body = await bounded_json(request)
        try:
            return await answer_name_check(db, session, ref, entity_id, body)
        except RecapError as err:
            status, detail = request_status.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/recaps/{ref}/audio")
    async def me_recap_audio(ref: str, signed: Signed) -> dict[str, Any]:
        """Short-lived links to this recap's narration and music (#1418). Only for a DM of the
        campaign (the same rule as reading the recap), and only while the audio was made from the
        recap's text as it is now."""
        session, _token = signed
        try:
            recap = await read_recap(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None
        if not recap["audio"]["current"]:
            return {"links": {}}
        return {"links": audio_links.links(recap_files, settings.secret_key, settings.api_url, ref)}

    @app.post("/me/recaps/{ref}/audio/again")
    async def me_recap_audio_again(ref: str, signed: Signed) -> dict[str, Any]:
        """ "Make the audio again": at most once in 10 minutes for a recap, the database's rule."""
        session, _token = signed
        if not settings.recaps_on:  # DMBOT_RECAPS is off: nothing would make it
            raise HTTPException(status_code=409, detail="audio_off")
        try:
            facts = await read_choices(
                db, session, ref.rsplit(".", 1)[0], now=int(clock()),
                enforce_plans=settings.enforce_plans,
            )  # fmt: skip
            return await audio_again(db, session, ref, audio_in_plan=bool(facts["canAudio"]))
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.post("/me/recaps/{ref}/approve")
    async def me_recap_approve(ref: str, signed: Signed, request: Request) -> Any:
        """✅ Approve the text and pick what to make (#2083): `{"choice": "voice" | "music" |
        "both" | "text"}`. Wrong cues refuse it (422 with `cue_problems`); an audio choice
        needs the campaign owner's plan to include it."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        choice = body.get("choice") if isinstance(body, dict) else None
        if not isinstance(choice, str):
            raise HTTPException(status_code=422, detail="bad_choice")
        try:
            facts = await read_choices(
                db, session, ref.rsplit(".", 1)[0], now=int(clock()),
                enforce_plans=settings.enforce_plans,
            )  # fmt: skip
            return await approve_recap(
                db,
                session,
                ref,
                choice,
                audio_in_plan=bool(facts["canAudio"]),
                outside_engine=settings.outside_engine,
            )
        except RecapError as err:
            if err.problems is not None:
                return JSONResponse(
                    {"error": "cue_problems", "cue_problems": err.problems}, status_code=422
                )
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    # The public, read-only link of a posted recap (#2118): no sign-in, the token is the key.
    # Per address, because anyone can call it; the same answer for every way it can fail.
    # The page itself is strict; the audio has its own, looser one (a player asks for a file in
    # several ranges). Behind a proxy, settings.client_ip_header must be set or every visitor shares
    # one address.
    shared_limit = RateLimit(60, count=30)
    shared_audio_limit = RateLimit(60, count=240)

    def shared_gate(request: Request, limit: RateLimit = shared_limit) -> None:
        verdict = limit.take(limit_key(client_address(request)))
        if verdict != "ok":
            raise HTTPException(
                status_code=429 if verdict == "wait" else 503,
                detail="slow_down" if verdict == "wait" else "busy",
                headers={"Retry-After": "60"},
            )

    @app.get("/recaps/{token}")
    async def shared_recap(token: str, request: Request) -> JSONResponse:
        shared_gate(request)
        found = await shared_recaps.read_shared(db, recap_files, token, settings.api_url)
        if found is None:
            raise HTTPException(status_code=404, detail="not_found")
        return JSONResponse(
            found, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}
        )

    @app.get("/recaps/{token}/audio/{kind}")
    async def shared_recap_audio(token: str, kind: str, request: Request) -> FileResponse:
        shared_gate(request, shared_audio_limit)
        path = await shared_recaps.audio_for(db, recap_files, token, kind)
        if path is None:
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(
            path,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )

    @app.post("/me/recaps/{ref}/share")
    async def me_recap_share(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        """Turn the link of a posted recap on or off, or make a new one: `{"action": "on" |
        "off" | "new"}`. DMs of the campaign only."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        action = body.get("action") if isinstance(body, dict) else None
        try:
            return await share_recap(
                db, session, ref, action if isinstance(action, str) else "",
                outside_engine=settings.outside_engine,
            )  # fmt: skip
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/recap-audio/{ref}/{kind}")
    async def recap_audio_file(ref: str, kind: str, e: str = "", s: str = "") -> FileResponse:
        """One audio file, for a link made by the route above. The signature is the whole check."""
        path = audio_links.file_for(recap_files, settings.secret_key, ref, kind, e, s)
        if path is None:
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(
            path, media_type="audio/mpeg", headers={"Cache-Control": "private, no-store"}
        )

    @app.get("/me/campaigns/{ref}/all-names")
    async def me_campaign_all_names(
        ref: str, signed: Signed, q: str = "", kind: str = "", sort: str = "name", page: int = 1
    ) -> dict[str, Any]:
        """A page of the campaign's names for the All names page (#2294). A DM gets everything; a
        server manager who is not a DM gets no secret names; anyone else gets "not found"."""
        session, _token = signed
        try:
            return await all_names.read_page(db, session, ref, q=q, kind=kind, sort=sort, page=page)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    name_refusals = {
        "not_found": (404, "not_found"),
        "not_allowed": (403, "not_allowed"),
        "bad_action": (400, "bad_action"),
        "bad_name": (400, "bad_name"),
        "bad_kind": (400, "bad_kind"),
        "too_long": (400, "too_long"),
        "needs_confirm": (400, "needs_confirm"),
        "bad_target": (400, "bad_target"),
        "too_soon": (429, "too_soon"),
    }

    @app.post("/me/campaigns/{ref}/all-names/{entity_id}", status_code=202)
    async def me_campaign_all_names_act(
        ref: str, entity_id: str, signed: Signed, request: Request
    ) -> dict[str, str]:
        """One DM action on a name (#2294): queued for the bot, which applies it the way the
        Discord name card does. The answer is polled at `.../all-names/answers/{answer}`."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        try:
            return {"answer": await all_names.queue_action(db, session, ref, entity_id, body)}
        except RecapError as err:
            status, detail = name_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/all-names/answers/{answer}")
    async def me_campaign_all_names_answer(ref: str, answer: str, signed: Signed) -> dict[str, Any]:
        """Where the signed-in DM's own action stands: waiting, working, done (with the changed
        name's row) or failed (with why)."""
        session, _token = signed
        try:
            return await all_names.read_action(db, session, ref, answer)
        except RecapError as err:
            status, detail = name_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/names")
    async def me_campaign_names(ref: str, signed: Signed) -> dict[str, Any]:
        """The campaign's confirmed public names with the narrator's spelling (#1451). DM only; a
        player or another campaign's DM gets "not found". Never a secret name or a person."""
        session, _token = signed
        try:
            return {"names": await list_names(db, session, ref)}
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.put("/me/campaigns/{ref}/names/{alias_id}/said-as")
    async def me_campaign_name_said_as(
        ref: str, alias_id: str, signed: Signed, request: Request
    ) -> dict[str, bool]:
        """Set or clear how the narrator says one name: `{"saidAs": "Kah-zeth"}` or `null`."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        # The key must be there: a body without it is a mistake, not a request to clear.
        said = body.get("saidAs") if isinstance(body, dict) and "saidAs" in body else 0
        if said is not None and not isinstance(said, str):
            raise HTTPException(status_code=422, detail="bad_text")
        try:
            await set_said_as(db, session, ref, alias_id, said)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None
        return {"ok": True}

    @app.get("/me/campaigns/{ref}/recap-choices")
    async def me_recap_choices(ref: str, signed: Signed) -> dict[str, Any]:
        """The DM's three recap choices for a campaign (#1468). DM only; anyone else gets "not
        found"."""
        session, _token = signed
        try:
            return await read_choices(
                db, session, ref, now=clock(), enforce_plans=settings.enforce_plans
            )
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.post("/me/campaigns/{ref}/recap-choices")
    async def me_recap_choice(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        """Change one choice: `{"which": "text"|"audio"|"email", "on": true|false}` (#1468)."""
        session, _token = signed
        try:
            body = await request.json()
        except ValueError:
            body = None
        which = body.get("which") if isinstance(body, dict) else None
        on = body.get("on") if isinstance(body, dict) else None
        if not isinstance(which, str) or not isinstance(on, bool):
            raise HTTPException(status_code=422, detail="bad_choice")
        try:
            return await set_choice(
                db, session, ref, which, on, now=clock(), enforce_plans=settings.enforce_plans
            )
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    # A campaign's session transcripts, for anyone in the server (#1501): shared by the project's
    # rule, so not DM-only. The words are the same text Discord's download carries.
    @app.get("/me/transcripts")
    async def me_transcript_campaigns(signed: Signed) -> dict[str, Any]:
        """The campaigns with a saved transcript in the person's servers (those the plan rule lets
        them read)."""
        session, _token = signed
        return {
            "campaigns": await transcript_pages.list_campaigns(
                db, session, now=clock(), enforce_plans=settings.enforce_plans
            )
        }

    # Importing older sessions (#2105): DMs of the campaign only (the doors check), paid plans only.
    # The files travel in the JSON body as text ({name, text}); nothing is kept or logged.
    # Importing is the heaviest thing the API does: a few a minute for a person is plenty.
    import_limit = RateLimit(60, count=6)

    async def imports_enabled(ref: str) -> None:
        """Off by default (DMBOT_IMPORTS): the routes do not exist for anyone until dev1 has
        checked a real import. DMBOT_IMPORTS_SERVERS turns them on for chosen servers alone
        (#2259); the server comes from the address, so a server that is off costs no lookup."""
        try:
            guild_id, _ = imports.ref_parts(ref)
        except RecapError:
            raise HTTPException(status_code=404, detail="not_found") from None
        if not settings.imports_enabled_for(guild_id):
            raise HTTPException(status_code=404, detail="not_found")

    async def import_body(request: Request, session: Session) -> dict[str, Any]:
        """The JSON body, read with a running size cap (a declared length is not trusted) and
        parsed away from the event loop. Counted per account before anything is read."""
        key = f"import:{session.user_id}"
        if import_limit.take(key) != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        length = request.headers.get("content-length", "")
        if length.isdecimal() and int(length) > imports.MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="too_big")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > imports.MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="too_big")
            chunks.append(chunk)
        try:
            body = await asyncio.to_thread(json.loads, b"".join(chunks))
        except ValueError:
            raise HTTPException(status_code=422, detail="bad_files") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="bad_files")
        return body

    def import_refusal(err: RecapError) -> HTTPException:
        status, detail = recap_refusals.get(err.code, (404, "not_found"))
        return HTTPException(status_code=status, detail=detail)

    @app.post("/me/campaigns/{ref}/imports/preview", dependencies=[Depends(imports_enabled)])
    async def me_import_preview(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        try:  # the DM and the plan come first: nothing is read for anyone who may not import
            await imports.authorize(
                db, session, ref, outside=settings.outside_engine,
                now=int(clock()), enforce_plans=settings.enforce_plans,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None
        body = await import_body(request, session)
        try:
            return await imports.preview(
                db, session, ref, body.get("files"), links=body.get("links"),
                outside=settings.outside_engine, now=int(clock()),
                enforce_plans=settings.enforce_plans, site_recaps_on=settings.recaps_on,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None

    @app.get("/me/campaigns/{ref}/imports/members", dependencies=[Depends(imports_enabled)])
    async def me_import_members(ref: str, signed: Signed, q: str = "") -> dict[str, Any]:
        session, _token = signed
        try:
            return await imports.search_members(
                db, session, ref, q, outside=settings.outside_engine,
                now=int(clock()), enforce_plans=settings.enforce_plans,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None

    @app.post("/me/campaigns/{ref}/imports/check", dependencies=[Depends(imports_enabled)])
    async def me_import_check(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        try:  # the DM and the plan come first: nothing is read for anyone who may not import
            await imports.authorize(
                db, session, ref, outside=settings.outside_engine,
                now=int(clock()), enforce_plans=settings.enforce_plans,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None
        body = await import_body(request, session)
        try:
            return await imports.check(
                db, session, ref, body.get("files"), body.get("mapping"),
                links=body.get("links"), outside=settings.outside_engine, now=int(clock()),
                enforce_plans=settings.enforce_plans,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None

    @app.post(
        "/me/campaigns/{ref}/imports", status_code=202, dependencies=[Depends(imports_enabled)]
    )
    async def me_import(ref: str, signed: Signed, request: Request) -> dict[str, Any]:
        session, _token = signed
        try:  # the DM and the plan come first: nothing is read for anyone who may not import
            await imports.authorize(
                db, session, ref, outside=settings.outside_engine,
                now=int(clock()), enforce_plans=settings.enforce_plans,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None
        body = await import_body(request, session)
        try:
            return await imports.confirm(
                db, session, ref, body.get("files"), body.get("plan"),
                links=body.get("links"), outside=settings.outside_engine, now=int(clock()),
                enforce_plans=settings.enforce_plans, site_recaps_on=settings.recaps_on,
            )  # fmt: skip
        except RecapError as err:
            raise import_refusal(err) from None

    @app.get("/me/campaigns/{ref}/imports/{import_id}", dependencies=[Depends(imports_enabled)])
    async def me_import_progress(ref: str, import_id: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await imports.progress(
                db, session, ref, import_id, outside=settings.outside_engine
            )
        except RecapError as err:
            raise import_refusal(err) from None

    @app.get("/me/campaigns/{ref}/transcripts")
    async def me_transcripts(ref: str, signed: Signed) -> dict[str, Any]:
        """The campaign's sessions that have lines, newest first."""
        session, _token = signed
        try:
            return await transcript_pages.list_sessions(
                db, session, ref, now=clock(), enforce_plans=settings.enforce_plans
            )
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/transcripts/{number}")
    async def me_transcript(
        ref: str, number: int, signed: Signed, version: str = "cleaned", offset: int = 0
    ) -> dict[str, Any]:
        """One session to read: `?version=cleaned` (the default) or `heard`, a page of lines from
        `?offset=` (the answer's `next` is the offset of the following page)."""
        session, _token = signed
        try:
            return await transcript_pages.read_session(
                db,
                session,
                ref,
                number,
                version,
                now=clock(),
                enforce_plans=settings.enforce_plans,
                offset=offset,
            )
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/transcripts/{number}/download")
    async def me_transcript_download(
        ref: str, number: int, signed: Signed, version: str = "cleaned"
    ) -> Response:
        """The same text file Discord's download button gives, `export.render`'s bytes."""
        session, _token = signed
        try:
            name, text = await transcript_pages.render(
                db,
                session,
                ref,
                number,
                version,
                now=clock(),
                enforce_plans=settings.enforce_plans,
            )
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None
        return Response(
            text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            # `name` is a slug of letters, digits and dashes (export.file_name): safe in a header.
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # A campaign's house rules, for anyone in the server (#1521): the table's rules, which every
    # member may read in Discord; no plan rule, as the bot has none for them.
    @app.get("/me/house-rules")
    async def me_house_rule_campaigns(signed: Signed) -> dict[str, Any]:
        """The campaigns with a house rule or a followed set in the person's servers."""
        session, _token = signed
        return {"campaigns": await house_rule_pages.list_campaigns(db, session)}

    @app.get("/me/campaigns/{ref}/house-rules")
    async def me_house_rules(ref: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await house_rule_pages.read_rules(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/house-rules/download")
    async def me_house_rules_download(ref: str, signed: Signed) -> Response:
        """The file the bot's Download button sends, byte for byte."""
        session, _token = signed
        try:
            name, text = await house_rule_pages.render(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None
        return Response(
            text.encode(),
            media_type="text/plain; charset=utf-8",
            # `name` is `house_file.filename`'s slug (letters, digits, dashes): safe in a header.
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # An NPC's page and the caller's private notes on it (#2265): any member of the campaign's
    # server. A note is read and written only by its own person; nothing here logs one.
    npc_note_limit = RateLimit(60, count=60)

    def npc_refusal(err: RecapError) -> HTTPException:
        status, detail = recap_refusals.get(err.code, (404, "not_found"))
        return HTTPException(status_code=status, detail=detail)

    @app.get("/me/campaigns/{ref}/npcs/{entry}")
    async def me_npc_page(ref: str, entry: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await npc_pages.read_page(db, session, ref, entry)
        except RecapError as err:
            raise npc_refusal(err) from None

    @app.put("/me/campaigns/{ref}/npcs/{entry}/note")
    async def me_npc_note(ref: str, entry: str, signed: Signed, request: Request) -> dict[str, Any]:
        """Save or clear my note: `{"body": "..."}` (empty clears it)."""
        session, _token = signed
        if npc_note_limit.take(f"npc-note:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        length = request.headers.get("content-length", "")
        if not length.isdecimal() or int(length) > npc_pages.NOTE_MAX * 8:
            raise HTTPException(status_code=413, detail="too_big")  # (read nothing bigger)
        try:
            body = await request.json()
        except ValueError:
            body = None
        text = body.get("body") if isinstance(body, dict) else None
        try:
            await npc_pages.save_note(db, session, ref, entry, text)
        except RecapError as err:
            raise npc_refusal(err) from None
        return {"ok": True}

    @app.get("/me/npc-notes")
    async def me_npc_notes(campaign: str, signed: Signed) -> Response:
        """All of my notes in one campaign, as a file (including those on names no longer
        listed)."""
        session, _token = signed
        try:
            found = await npc_pages.my_notes(db, session, campaign)
        except RecapError as err:
            raise npc_refusal(err) from None
        return Response(
            json.dumps(found, ensure_ascii=False, indent=2).encode(),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="my-npc-notes.json"'},
        )

    @app.delete("/me/npc-notes")
    async def me_npc_notes_delete(campaign: str, signed: Signed) -> dict[str, Any]:
        """Delete all of my notes in one campaign."""
        session, _token = signed
        if npc_note_limit.take(f"npc-note:{session.user_id}") != "ok":
            raise HTTPException(status_code=429, detail="too_soon")
        try:
            return {"deleted": await npc_pages.delete_my_notes(db, session, campaign)}
        except RecapError as err:
            raise npc_refusal(err) from None

    # A campaign's homebrew the DM let the players know (#1525): any member of the server; hidden
    # entries never leave the database. No plan rule, as the bot has none for homebrew.
    @app.get("/me/homebrew")
    async def me_homebrew_campaigns(signed: Signed) -> dict[str, Any]:
        """The campaigns with a shown homebrew entry in the person's servers."""
        session, _token = signed
        return {"campaigns": await homebrew_pages.list_campaigns(db, session)}

    @app.get("/me/campaigns/{ref}/homebrew")
    async def me_homebrew(ref: str, signed: Signed) -> dict[str, Any]:
        session, _token = signed
        try:
            return await homebrew_pages.read_homebrew(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.get("/me/campaigns/{ref}/homebrew/download")
    async def me_homebrew_download(ref: str, signed: Signed) -> Response:
        """The bot's homebrew file for the shown entries, byte for byte."""
        session, _token = signed
        try:
            name, text = await homebrew_pages.render(db, session, ref)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None
        return Response(
            text.encode(),
            media_type="text/plain; charset=utf-8",
            # `name` is `homebrew_file.filename`'s slug (letters, digits, dashes): safe in a header.
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/me/recaps/{ref}/{action}")
    async def me_recap_decide(ref: str, action: str, signed: Signed) -> dict[str, Any]:
        """Post to the table (the bot sends it), Try again, or Don't post."""
        session, _token = signed
        try:
            return await decide(db, session, ref, action)
        except RecapError as err:
            status, detail = recap_refusals.get(err.code, (404, "not_found"))
            raise HTTPException(status_code=status, detail=detail) from None

    @app.post("/me/recording/{server_id}/stop", status_code=204)
    async def stop_recording(server_id: str, signed: Signed) -> Response:
        """Stop recording the signed-in person in one of their servers (#1151): the same stop
        the bot's Menu writes (the consent store's own revoke). The notification to the bot
        goes out in the same transaction as the delete, so it is delivered when the delete
        commits, and only when a consent was removed. Only their own consent, only in servers
        on their session's list; asking twice is fine. Starting again stays in Discord."""
        session, _token = signed
        try:
            guild_id = int(server_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found") from None
        if guild_id not in {int(g.id) for g in session.guilds}:
            raise HTTPException(status_code=404, detail="not_found")

        async def tell_the_bot(conn: Any) -> None:
            await consent_notify.send(conn, guild_id, session.user_id)

        scoped = db.as_person(session.user_id, session.id_hash)
        await ConsentStore(scoped).revoke(guild_id, session.user_id, in_transaction=tell_the_bot)
        log.info("A recording stop was made on the website")
        return Response(status_code=204)

    @app.get("/me/export")
    async def me_export(signed: Signed) -> Response:
        """Download everything DMbot holds about the signed-in person (#1150). Their own
        rows only. The log says that an export happened, never what was in it."""
        session, _token = signed
        taken = export_limit.take(f"user-{session.user_id}")
        if taken != "ok":
            raise HTTPException(status_code=429, detail="slow_down" if taken == "wait" else "busy")
        try:
            data = await build_export(db, session, now=clock())
        except BaseException:
            export_limit.give_back(f"user-{session.user_id}")  # a failed export costs no turn
            raise
        log.info("A data export was made")
        return Response(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="dmbot-my-data.json"',
                "Cache-Control": "no-store",
            },
        )

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
        add_on = await entitlements_writer.recap_audio_subscription(db, session.user_id)
        if add_on is not None:  # the recap audio add-on is a second subscription (#1389)
            if payments is None:
                raise HTTPException(status_code=503, detail="payments_unavailable")
            try:
                await payments.cancel(subscription_id=add_on)
            except PaymentError as exc:
                log.warning("Couldn't stop the recap audio add-on for a deletion: %s", exc)
                raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        await delete_person(db, session.user_id, now=clock())
        log.info("Account deleted: user %s", session.user_id)
        response = Response(status_code=204)
        response.delete_cookie(session_cookie, path="/", secure=settings.secure_cookies)
        return response

    # Plans and payments

    @app.post("/plan/try-it", status_code=204)
    async def try_it(signed: Signed) -> Response:
        session, _token = signed
        _plan, access = await entitlements.plan_and_access(db, session.user_id, clock())
        if access.kind in ("free", "grant"):
            # Free access covers them: their one trial isn't used up for nothing (#771).
            raise HTTPException(status_code=409, detail="has_free_access")
        result = await entitlements_writer.start_try_it(db, session.user_id, now=clock())
        if not result.started:
            # "try_it_used": only once per Discord user; "has_plan": a plan already works.
            raise HTTPException(status_code=409, detail=f"try_it_{result.reason}")
        log.info("Try It started: user %s", session.user_id)
        return Response(status_code=204)

    # Hand-over offers (#614): answered with the same store code as the bot's buttons.

    async def answer_offer(signed: tuple[Session, str], ref: str, what: offers.Answer) -> Response:
        session, _token = signed
        outcome = await offers.answer(
            db, session, ref, what, now=clock(), enforce_plans=settings.enforce_plans
        )
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
        current, access = await entitlements.plan_and_access(db, session.user_id, clock())
        if access.kind in ("free", "grant"):
            # Free access covers them: never a price or a payment (#771).
            raise HTTPException(status_code=409, detail="has_free_access")
        if current is not None and current.plan != "try-it" and current.status != "lapsed":
            # A paid plan is working: change it on the billing page, never a second
            # subscription.
            raise HTTPException(status_code=409, detail="has_paid_plan")
        email = await account_email(db, session.user_id)
        try:
            url = await provider.checkout_url(
                user_id=session.user_id,
                email=email,
                plan=plan,
                return_url=account_page,
                dmbot_id=await dmbot_id(db, session.user_id),
                customer_id=await customer_id(db, session.user_id),
            )
        except PaymentError as exc:
            log.warning("Checkout failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/billing/extra-hours")
    async def extra_hours(signed: Signed) -> dict[str, str]:
        """The page to buy +10 hours for this month (#1089). Only for a paid plan that
        works now (active, or in the payment grace): the writer ignores hours bought on
        Try It or without a plan, so selling them there would take money for nothing."""
        provider = require_payments()
        session, _token = signed
        current, access = await entitlements.plan_and_access(db, session.user_id, clock())
        if access.kind in ("free", "grant"):
            raise HTTPException(status_code=409, detail="has_free_access")
        # usable(), not just "not lapsed": an overdue plan can't use the hours either, and
        # the page then refreshes to what is true now.
        if current is None or current.plan == "try-it" or not current.usable(clock()):
            raise HTTPException(status_code=409, detail="no_paid_plan")
        email = await account_email(db, session.user_id)
        try:
            url = await provider.extra_hours_checkout_url(
                user_id=session.user_id,
                email=email,
                return_url=account_page,
                dmbot_id=await dmbot_id(db, session.user_id),
                customer_id=await customer_id(db, session.user_id),
            )
        except PaymentError as exc:
            log.warning("Extra hours checkout failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/billing/recap-audio")
    async def recap_audio(signed: Signed) -> dict[str, str]:
        """The page to subscribe to the recap audio add-on (#1389). Hidden (404) until its price
        is set, as if the route did not exist. Only for a working paid plan that does not
        already have spoken recaps: Guild and Pro include them, free access has them, and a
        second subscription would charge for nothing."""
        provider = require_payments()
        if not provider.recap_audio_for_sale:
            raise HTTPException(status_code=404, detail="not_found")
        session, _token = signed
        current, access = await entitlements.plan_and_access(db, session.user_id, clock())
        if access.recap_audio:
            raise HTTPException(status_code=409, detail="already_included")
        if current is None or current.plan == "try-it" or not current.usable(clock()):
            raise HTTPException(status_code=409, detail="no_paid_plan")
        if not current.has_customer:
            # A plan set by hand has no customer at the company: the add-on's would be a
            # different one, and its events could not follow an account move (#1230).
            raise HTTPException(status_code=409, detail="no_paid_plan")
        if await entitlements_writer.recap_audio_subscription(db, session.user_id) is not None:
            # One already exists (it may have run out unpaid): a second would charge twice.
            raise HTTPException(status_code=409, detail="already_subscribed")
        email = await account_email(db, session.user_id)
        try:
            url = await provider.recap_audio_checkout_url(
                user_id=session.user_id,
                email=email,
                return_url=account_page,
                dmbot_id=await dmbot_id(db, session.user_id),
                customer_id=await customer_id(db, session.user_id),
            )
        except PaymentError as exc:
            log.warning("Recap audio checkout failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/billing/portal")
    async def billing_portal(signed: Signed, request: Request) -> dict[str, str]:
        provider = require_payments()
        session, _token = signed
        # Only Change plan asks for the plan list; Fix my payment and Stop paying want the
        # ordinary page (#2165).
        try:
            body = await request.json()
            wants_plans = isinstance(body, dict) and body.get("flow") == "plans"
        except ValueError:
            wants_plans = False
        plan = await entitlements.get(db, session.user_id)
        customer = await customer_id(db, session.user_id)
        if plan is None or customer is None:
            raise HTTPException(status_code=409, detail="no_paid_plan")
        try:
            # Change plan opens straight on choosing a plan (#2165), when there is a subscription
            # to change; the ordinary page opens if the company refuses that.
            url = await provider.billing_url(
                customer_id=customer,
                return_url=account_page,
                subscription_id=(
                    await entitlements_writer.plan_subscription(db, session.user_id)
                    if wants_plans
                    else None
                ),
            )
        except PaymentError as exc:
            log.warning("Billing page failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        return {"url": url}

    @app.post("/billing/renew")
    async def billing_renew(signed: Signed) -> dict[str, bool]:
        """Renew (#2165): undo a cancellation that has not taken effect yet. The plan runs on to
        its date and renews as before, with no new charge. Only for a paid plan that was
        cancelled and still works."""
        provider = require_payments()
        session, _token = signed
        plan = await entitlements.get(db, session.user_id)
        subscription = await entitlements_writer.plan_subscription(db, session.user_id)
        # Only a plan that is paid up: one in its payment grace has a failed invoice to fix first.
        if plan is None or plan.ends_at is None or subscription is None or plan.status != "active":
            raise HTTPException(status_code=409, detail="not_ending")
        if plan.ends_at <= int(clock()):
            raise HTTPException(status_code=409, detail="not_ending")  # it has run out already
        try:
            await provider.resume(subscription_id=subscription)
        except NotResumable as exc:
            # Ended, or its payment is overdue: nothing to renew from here.
            log.warning("Renew not possible: %s", exc)
            raise HTTPException(status_code=409, detail="not_ending") from exc
        except ResumeRefused as exc:
            # The company won't undo it from here (a plan change is scheduled, say): its billing
            # page can. The page says so.
            log.warning("Renew refused by the company: %s", exc)
            raise HTTPException(status_code=409, detail="use_billing_page") from exc
        except PaymentError as exc:
            log.warning("Renew failed: %s", exc)
            raise HTTPException(status_code=502, detail="payments_unavailable") from exc
        await entitlements_writer.clear_cancel(db, session.user_id, now=int(clock()))
        return {"ok": True}

    async def release_recap_audio(user_id: int) -> None:
        """Cancel this person's add-on subscription at the company and clear it here (#1389):
        its plan ended, or now includes audio, so it would only keep charging. Never raises: a
        failure is logged loudly for a person (the add-on gives nothing without a working plan,
        and the entitlement checks that, so nobody gets it free)."""
        try:
            sub = await entitlements_writer.recap_audio_subscription(db, user_id)
            if sub is None or payments is None:
                return
            try:
                await payments.cancel(subscription_id=sub)
            except Exception:
                await strays.note(db, sub)  # the hourly sweep tries again (#1399)
                raise
            await entitlements_writer.forget_recap_audio(db, user_id, now=clock())
        except Exception as exc:  # the company, the database: nothing here may fail the webhook
            log.error("Couldn't cancel a recap audio add-on: %s", type(exc).__name__)

    async def cancel_stray_add_on(subscription_id: str | None) -> None:
        """An add-on subscription that must not exist (a second one, or one bought with no
        working plan): cancelled at the company so it stops charging."""
        if subscription_id is None or payments is None:
            return
        try:
            await payments.cancel(subscription_id=subscription_id)
        except Exception as exc:
            await strays.note(db, subscription_id)  # the hourly sweep tries again (#1399)
            log.error(
                "Couldn't cancel a stray recap audio subscription: %s: kept for a retry",
                type(exc).__name__,
            )

    async def recap_audio_follow_up(event: Any, outcome: str) -> None:
        """What a payment event means for the add-on's billing, after it is applied (#1389)."""
        if event.kind == "subscription_ended" and outcome == "applied":
            await release_recap_audio(event.user_id)  # the add-on goes with the plan
        elif (
            event.kind in ("subscription_started", "subscription_renewed")
            and outcome == "applied"
            and event.plan in entitlements.RECAP_AUDIO_PLANS
        ):
            await release_recap_audio(event.user_id)  # the plan now includes audio
        elif event.kind in ("recap_audio_started", "recap_audio_renewed") and outcome == "ignored":
            await cancel_stray_add_on(event.subscription_id)

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
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            log.error("A signed payment event couldn't be read: needs a person to look at it")
            raise HTTPException(status_code=400, detail="bad_event") from None
        if event is None:
            return Response(status_code=200)  # an event DMbot doesn't use
        if event.kind in ("plan_scheduled", "plan_unscheduled") and event.user_id == 0:
            # A plan schedule doesn't name the person: ask the company whose subscription it is.
            # One that isn't a plan's (the add-on, or made outside DMbot) is not ours.
            try:
                owner = (
                    None
                    if event.subscription_id is None
                    else await payments.subscription_owner(subscription_id=event.subscription_id)
                )
            except PaymentError as exc:
                log.warning("Couldn't find whose plan a schedule is: %s", exc)
                raise HTTPException(status_code=503, detail="retry_later") from exc
            if owner is None:
                return Response(status_code=200)
            event = dataclasses.replace(event, user_id=owner)
        outcome = await entitlements_writer.apply_event(db, event, now=clock())
        log.info("Payment event %s for user %s: %s", event.event_id, event.user_id, outcome)
        if outcome == "retry":
            # Too early (its "started" hasn't arrived): the company delivers it again later.
            raise HTTPException(status_code=503, detail="retry_later")
        if outcome == "rejected":
            raise HTTPException(status_code=422, detail="bad_event")
        await recap_audio_follow_up(event, outcome)
        return Response(status_code=200)

    # The "Say hello" forms (#665): no sign-in needed.

    def client_address(request: Request) -> str:
        # Behind Cloudflare and Caddy the connection comes from the proxy, so the
        # visitor's own address is in the header the proxy sets (settings.client_ip_header).
        # Only that header, and only when it holds a real address.
        if settings.client_ip_header:
            forwarded = request.headers.get(settings.client_ip_header, "").strip()
            with contextlib.suppress(ValueError):
                return str(ipaddress.ip_address(forwarded))
        return request.client.host if request.client else "unknown"

    @app.post("/email/unsubscribe")
    async def email_unsubscribe(request: Request) -> dict[str, bool]:
        """The unsubscribe link in an offer email (#1229): the page sends the `uid` and `t`
        from the link. A signed token proves the link came from us, so this needs no
        sign-in; it never says whether the person has an account."""
        body = await request.body()
        if len(body) > 1024:
            raise HTTPException(status_code=413, detail="too_long")
        try:
            raw = json.loads(body)
            uid, token = str(raw["uid"]), str(raw["t"])
            user_id = from_dmbot_id(uid)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="bad_link") from None
        if not check_unsubscribe_token(uid, token, settings.link_token_secret):
            raise HTTPException(status_code=400, detail="bad_link")
        # The link carries the ID the account was made with; the account may have moved to a
        # new Discord ID since (#1230).
        current = await user_of_dmbot_id(db, uid)
        await mail.unsubscribe(db, current or user_id, now=int(clock()))
        return {"ok": True}

    @app.post("/feedback")
    async def send_feedback(request: Request) -> dict[str, str]:
        if discussions is None:
            raise HTTPException(status_code=503, detail="feedback_off")
        with contextlib.suppress(ValueError):
            if int(request.headers.get("content-length", "0")) > MAX_FEEDBACK_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_FEEDBACK_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):  # RecursionError: "[[[[..." nested too deep
            raw = None
        message = feedback.read_message(raw)
        if isinstance(message, str):
            raise HTTPException(status_code=400, detail=message)
        address = client_address(request)
        turn = feedback.rate_key(address)
        taken = feedback_limit.take(turn)
        if taken != "ok":
            raise HTTPException(status_code=429, detail="slow_down" if taken == "wait" else "busy")
        try:
            if human_check is not None:
                token = str((raw.get("turnstile") if isinstance(raw, dict) else None) or "")
                if not await human_check.verify(token, address):
                    if token:
                        # A wrong answer uses the turn: otherwise one address could make
                        # us ask Cloudflare without end. No answer at all costs nothing.
                        raise _Refused("not_human")
                    raise HTTPException(status_code=400, detail="not_human")
            # Once GitHub has the post, the turn is used, whatever happens next.
            posted = await feedback.post(db, discussions, message, now=clock())
        except _Refused as refused:
            raise HTTPException(status_code=400, detail=refused.code) from None
        except FeedbackError as exc:
            feedback_limit.give_back(turn)
            log.warning("Feedback couldn't be posted: %s", exc)
            raise HTTPException(status_code=502, detail="feedback_unavailable") from exc
        except BaseException:
            feedback_limit.give_back(turn)
            raise
        log.info("Feedback posted: discussion %s", posted.number)
        return {"url": posted.url}

    @app.post("/support", status_code=201)
    async def send_support(request: Request, signed: Signed) -> dict[str, bool]:
        """A private support ticket from a signed-in person with an account (#1340, #1435: Try It
        too). Never public: it goes to
        the support_tickets table (support.py), and its words are never logged or emailed."""
        session, _token = signed
        with contextlib.suppress(ValueError):
            if int(request.headers.get("content-length", "0")) > MAX_SUPPORT_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_SUPPORT_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):  # RecursionError: "[[[[..." nested too deep
            raw = None
        ticket = support.read_ticket(raw)
        if isinstance(ticket, str):
            raise HTTPException(status_code=400, detail=ticket)
        now = clock()
        plan, _grant, access = await entitlements.plan_grant_access(db, session.user_id, now)
        if not support.may_write(plan, access):
            # No account yet: pointed to the public Q&A, on the page itself.
            raise HTTPException(status_code=403, detail="not_paying")
        if support.is_free(plan, now):
            waiting, today = await support.recent(db, session.user_id, now=now)
            if (why := free_ticket_refusal(waiting, today)) is not None:
                raise HTTPException(status_code=429, detail=why)
        # Free access may have no plan row: its kind is the label kept with the ticket.
        plan_label = plan.plan if plan else access.kind
        turn = str(session.user_id)
        taken = support_limit.take(turn)
        if taken != "ok":
            raise HTTPException(status_code=429, detail="slow_down" if taken == "wait" else "busy")
        try:
            if human_check is not None:
                address = client_address(request)
                token = str((raw.get("turnstile") if isinstance(raw, dict) else None) or "")
                if not await human_check.verify(token, address):
                    if token:
                        # A wrong answer uses the turn, so guessing Turnstile answers is slow.
                        raise _Refused("not_human")
                    raise HTTPException(status_code=400, detail="not_human")
            ticket_id = await support.add(db, session.user_id, plan_label, ticket, now=now)
        except _Refused as refused:
            raise HTTPException(status_code=400, detail=refused.code) from None
        except FeedbackError as exc:
            support_limit.give_back(turn)
            log.warning("A support ticket's human check couldn't run: %s", exc)
            raise HTTPException(status_code=502, detail="support_unavailable") from exc
        except BaseException:
            support_limit.give_back(turn)
            raise
        # Ids only: never the subject or the message.
        log.info("Support ticket added by user %s", session.user_id)
        if settings.support_team_email and mailer is not None:
            # The ticket is already kept: a failed email loses nothing, the admin page lists it.
            await mailer.notify_team(
                settings.support_team_email,
                "A DMbot support ticket is waiting",
                "An account sent a support ticket.\n\n"
                f"DMbot ID: {to_dmbot_id(session.user_id)}\nPlan: {plan_label}\n\n"
                f"Read it on the admin page: {settings.site_url}/admin\n",
                sender=mailer.settings.alerts_sender,  # (#1431: alerts come from their own address)
            )
        if helper is not None:
            await helper.acknowledge(ticket_id)  # one short thank-you; never raises
        # Whether the team can reply by email: the thank-you says so, or says what to do (#1340).
        return {"ok": True, "reply": await account_email(db, session.user_id) is not None}

    inbound_counts: dict[str, int] = {}
    inbound_seen: dict[str, float] = {}  # webhook ids handled lately: a retry is not read twice

    @app.post("/mail/inbound")
    async def inbound_mail(request: Request) -> dict[str, bool]:
        """A person's emailed reply to a support ticket (#1431), sent by the mail service's
        inbound webhook. Signed (Svix style) with MAIL_INBOUND_SECRET; without the secret the
        route does not exist. A message to no ticket, with a wrong token, or from any address but
        the ticket owner's account address is dropped and only counted (the log never holds who or
        what), and the service is still told "ok" so it does not send the same mail again."""
        if not settings.mail_inbound_secret or helper is None:
            raise HTTPException(status_code=404, detail="not_found")
        with contextlib.suppress(ValueError):
            if int(request.headers.get("content-length", "0")) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="too_long")
        if not verify_signature(
            settings.mail_inbound_secret, request.headers, body, now=float(clock())
        ):
            raise HTTPException(status_code=401, detail="bad_signature")

        message_id = request.headers.get("svix-id", "")
        moment = float(clock())
        for old in [k for k, at in inbound_seen.items() if moment - at > 600]:
            del inbound_seen[old]
        if message_id in inbound_seen:
            return {"ok": True}  # a retry of a message already read
        inbound_seen[message_id] = moment

        def dropped(why: str) -> dict[str, bool]:
            inbound_counts[why] = inbound_counts.get(why, 0) + 1
            log.info("Inbound support mail dropped (%s); %d so far", why, inbound_counts[why])
            return {"ok": True}

        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            return dropped("not_json")
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]  # some services wrap the message
        if not isinstance(data, dict):
            return dropped("not_json")
        ticket_id = ticket_of(recipients(data.get("to")), settings.link_token_secret)
        if ticket_id is None:
            return dropped("bad_token")
        found = await support.one(db, ticket_id)
        owner = (found.email or "").lower() if found is not None else ""
        if found is None or not owner or sender_address(data.get("from")) != owner:
            return dropped("unknown_sender")
        text = data.get("text")
        sent = await helper.sent_emails(ticket_id) if isinstance(text, str) else []
        words = fresh_words(text, sent=sent) if isinstance(text, str) else ""
        if not words:
            return dropped("empty")
        if not await helper.customer_replied(ticket_id, words):
            return dropped("not_kept")
        log.info("Inbound support mail kept on ticket %s", ticket_id)  # the number only
        return {"ok": True}

    @app.get("/support/mine")
    async def my_support(signed: Signed) -> dict[str, Any]:
        """The signed-in person's own questions to the team, newest first (#1373): when they were
        sent, the subject and where each stands. Never the words and never anyone else's. `reply`
        says whether the team can answer by email, so "answered" can tell them where to look."""
        session, _token = signed
        rows = await support.mine(db, session.user_id)
        reply = bool(rows) and await account_email(db, session.user_id) is not None
        return {
            "tickets": [
                {
                    "at": r.created_at,
                    "subject": r.subject,
                    "status": r.status,
                    # Who wrote the latest answer and its first line (#1432); null before one.
                    "answeredBy": {"ai": "helper", "person": "team"}.get(r.answered_by or ""),
                    "latest": r.latest,
                }
                for r in rows
            ],
            "reply": reply,
        }

    @app.get("/support/mine/{sent}")
    async def my_support_thread(sent: int, signed: Signed) -> dict[str, Any]:
        """The whole conversation of one of the signed-in person's own questions (#2193): their
        words, the helper's answers, their emailed replies and the team's, oldest first, each
        with who wrote it and when. The question is named by its sent time, as the list gives
        it. Another account's question and a missing one both answer 404. Nothing is logged."""
        session, _token = signed
        # A time outside BIGINT can't name a ticket, and psycopg would send it as numeric (a 500).
        if not 0 <= sent < 2**63:
            raise HTTPException(status_code=404, detail="not_found")
        turns = await support.my_thread(db, session.user_id, sent)
        if turns is None:
            raise HTTPException(status_code=404, detail="not_found")
        return {
            "messages": [{"who": t.who, "text": t.text, "at": t.created_at} for t in turns],
        }

    # The admin page's sign-in (#772); off while ADMIN_EMAILS is empty. Its sessions and
    # locks count seconds on their own steady clock (not `clock`, the wall time used for
    # signed tokens), so a changed system time can't end or extend them. Only the spent
    # Google states use `clock`, as they mirror the signed sign-in cookie's own life.
    app.include_router(
        admin_api.router(
            settings,
            db=db,
            google=google,
            helper=helper,
            admin_sessions=admin_sessions or AdminSessions(),
            tries=admin_tries or FailedTries(),
            client_address=client_address,
            clock=clock,
        )
    )

    return app
