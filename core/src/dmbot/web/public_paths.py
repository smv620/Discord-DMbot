"""Which paths of the web API the outside world may reach (#2625).

The Cloudflare tunnel used to decide this, from a list of routes typed into the owner's
dashboard (docs/DEPLOY.md). A path that was left out got Cloudflare's empty 404 before it reached
the app, and nothing said which routes were closed on purpose and which were forgotten: recap
audio links pointed at a path nobody had added. The list now lives here, where it is reviewed,
tested and deployed like the rest, and the tunnel has one route that passes everything.

Two lists, and a test that keeps every route of the app on one of them:
- `PUBLIC`: reachable through the tunnel, each with its reason and an example path.
- `INSIDE_ONLY`: served by the app, but not through the tunnel, each with its reason. A route
  that is not on either list fails CI, so a new route is decided when it is written.

A request through the tunnel carries `CF-Connecting-IP` (the tunnel always sets it). One for a
path that is not public gets the same empty 404 the tunnel gave before. A request from inside
(the Docker healthcheck, dev1 on the server) has no such header and is never affected. This only
narrows what the tunnel passes: it opens nothing the website's launch (#498) keeps shut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TUNNEL_HEADER = "CF-Connecting-IP"


@dataclass(frozen=True, slots=True)
class PublicPath:
    pattern: re.Pattern[str]  # matched against a request's path, from its start
    reason: str
    examples: tuple[str, ...]  # real paths it must pass: the sorting test and dev1's check use them


def _public(pattern: str, reason: str, *examples: str) -> PublicPath:
    return PublicPath(re.compile(pattern), reason, examples)


PUBLIC: tuple[PublicPath, ...] = (
    _public(r"/admin(/|$)", "the admin page and its Google sign-in (#836)", "/admin/auth/ways"),
    _public(
        r"/webhooks/stripe$",
        "the payment company's events, checked by their signature",
        "/webhooks/stripe",
    ),
    _public(r"/auth/", "sign-in with Discord or an emailed link, and sign-out", "/auth/logout"),
    _public(r"/me(/|$)", "the signed-in website's own calls", "/me", "/me/recaps"),
    _public(
        r"/billing/", "checkout and the billing portal, for signed-in people", "/billing/portal"
    ),
    _public(
        r"/mail/inbound$",
        "the mail service's inbound replies, checked by signature",
        "/mail/inbound",
    ),
    _public(r"/support(/|$)", "support tickets, signed in", "/support", "/support/mine"),
    _public(
        r"/recap-audio/",
        "a recap's narration on its page: each link is signed and expires (#2625)",
        "/recap-audio/ref/voice",
    ),
    _public(
        r"/recaps/",
        "a recap shared by its link, and its audio: the link's token is the key",
        "/recaps/token",
        "/recaps/token/audio/voice",
    ),
)

# The app's other routes. Each says why it is not public. Move one to PUBLIC when it should be.
CLOSED_UNTIL_LIVE = "a customer page: closed until the website goes live (#498)"
INSIDE_ONLY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/health$"), "the healthcheck; it must answer 404 from outside (docs/DEPLOY.md)"),
    (re.compile(r"/personal$"), CLOSED_UNTIL_LIVE),
    (re.compile(r"/install(/|$)"), CLOSED_UNTIL_LIVE + " (adding DMbot to a server)"),
    (re.compile(r"/servers/"), CLOSED_UNTIL_LIVE),
    (re.compile(r"/account/"), CLOSED_UNTIL_LIVE + " (deleting an account)"),
    (re.compile(r"/plan/"), CLOSED_UNTIL_LIVE + " (Try It)"),
    (re.compile(r"/offers/"), CLOSED_UNTIL_LIVE + " (hand-over offers)"),
    (re.compile(r"/email/"), CLOSED_UNTIL_LIVE + " (the unsubscribe page)"),
    (re.compile(r"/feedback$"), CLOSED_UNTIL_LIVE + " (the Say hello form)"),
    (re.compile(r"/webhooks/"), "any payment company other than Stripe is not exposed"),
)


def is_public(path: str) -> bool:
    return any(p.pattern.match(path) for p in PUBLIC)


def inside_only_reason(path: str) -> str | None:
    """Why the path is deliberately not public, or None when nobody has decided."""
    for pattern, reason in INSIDE_ONLY:
        if pattern.match(path):
            return reason
    return None
