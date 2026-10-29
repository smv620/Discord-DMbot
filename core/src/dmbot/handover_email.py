"""Handing a campaign over by email (#2532; docs/PLAN.md, "Hand-over by email").

The owner types an email address (in Discord, or on the account page). DMbot saves an offer that
holds only a SHA-256 of a one-use link's token, and emails the link to that address. Whoever opens
the link on the website, signs in and accepts (and has a paid plan with a free slot) becomes the
owner; nothing is accepted in Discord. The owner is only ever told "We've emailed the offer": never
whether the address has an account or a plan (#437 point 2). The address is not stored in the offer
and not logged; the mail log records that a handover email was sent, never its text.

This module is the one place that makes an offer, for the bot's form and the website's route alike:
the limit of five a day for each owner, the offer, the email, and taking the offer back if the
email could not be sent. At most one offer is open for a campaign; a new one takes back the old one.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Literal

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.db import Database
from dmbot.email_address import clean_email
from dmbot.mail import Mailer

log = logging.getLogger(__name__)

OFFERS_PER_DAY = 5
DAY_S = 24 * 3600
LINK_DAYS = 7

Outcome = Literal["emailed", "bad_email", "too_soon", "not_allowed", "email_failed"]

SENT = "We've emailed the offer."
BAD_EMAIL = (
    "That doesn't look like an email address (like name@example.com). Check it and press "
    "🤝 Hand over again."
)
TOO_SOON = (
    f"You've already sent {OFFERS_PER_DAY} offers in the last 24 hours. Please try again later."
)
NOT_ALLOWED = (
    "DMbot can't hand this campaign over right now. Only its owner can; check "
    "⚙️ Settings → 🗂️ Campaign management."
)
FAILED = "DMbot couldn't send that email, so nothing was offered. Try again in a minute."


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def email_text(*, owner: str, campaign: str, server: str, link: str) -> tuple[str, str]:
    """(subject, body) of the offer: PLAN's numbered steps, in its order. Nothing in it says
    whether the address already has a DMbot account. The owner's name is a display name anyone can
    choose, so it is quoted and never the first words of the message."""
    subject = f'{owner} offers you the DMbot campaign "{campaign}"'
    body = (
        f'The person named "{owner}" (on the Discord server "{server}") typed your email address '
        f'to offer you the D&D campaign "{campaign}" on DMbot. DMbot listens to a D&D table and '
        "privately helps the Dungeon Master.\n\n"
        f"To take it over, open this link within {LINK_DAYS} days:\n\n{link}\n\n"
        "What to do:\n"
        "1. Sign in to the DMbot website, with Discord or with your email address.\n"
        "2. You need a paid DMbot plan with a free campaign slot. The free Try It plan can't "
        "receive a campaign. If you don't have a plan yet, choose one first; the link keeps "
        f"working for {LINK_DAYS} days.\n"
        "3. Press Accept, and choose where the campaign runs: the Discord server it runs on now, "
        "or another server of yours (the page can add DMbot to a server that doesn't have it "
        "yet). The campaign then uses your plan's hours, and the person who offered it stays one "
        "of its DMs.\n"
        "4. If you play on a server where DMbot already runs this campaign, your Discord account "
        "simply becomes its DM there. Nothing needs installing.\n"
        "5. Then start a session as usual with /dmbot start.\n\n"
        "The link works once. Not expecting this? Ignore this email and nothing changes: the "
        "campaign stays with its owner."
    )
    return subject, body


async def offers_today(db: Database, owner_id: int, now: int) -> int:
    """How many offers by email this owner sent in the last day, over all their campaigns and
    servers (through the database's door to the mail log)."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_emails_since(%s, %s) AS n", (owner_id, now - DAY_S)
        )
        return int((await cur.fetchone() or {}).get("n") or 0)


async def offer_by_email(
    db: Database,
    store: CampaignStore,
    mailer: Mailer | None,
    *,
    owner_id: int,
    owner_name: str,
    guild_id: int,
    campaign_id: str,
    campaign_name: str,
    server_name: str,
    raw_email: object,
    site_url: str,
    now: int,
) -> Outcome:
    """Offer the campaign to an address. "emailed" whatever the address is. The order: check the
    address and the day's limit, save the offer, send the email, and if the send fails take the
    offer back, so nothing is left waiting that no one was told about."""
    email = clean_email(raw_email)
    if email is None:
        return "bad_email"
    if mailer is None or not site_url:
        return "email_failed"
    if await offers_today(db, owner_id, now) >= OFFERS_PER_DAY:
        return "too_soon"
    token = secrets.token_urlsafe(32)
    try:
        offer = await store.offer_handover_by_email(
            guild_id,
            campaign_id,
            owner_id,
            now,
            from_name=owner_name,
            token_hash=hash_token(token),
        )
    except CampaignError:
        return "not_allowed"
    subject, body = email_text(
        owner=owner_name,
        campaign=campaign_name,
        server=server_name,
        link=f"{site_url.rstrip('/')}/handover#{guild_id}-{offer.id}-{token}",
    )
    result = await mailer.send(
        user_id=owner_id,
        kind=f"handover:{guild_id}-{offer.id}",
        to=email,
        subject=subject,
        body=body,
    )
    if result != "sent":
        log.warning("A hand-over email was not sent: %s", result)
        try:
            await store.withdraw_handover(guild_id, offer.id, owner_id, now)
        except Exception:
            log.exception("Couldn't take back a hand-over offer whose email was not sent")
        return "email_failed"
    log.info("A hand-over was offered by email for campaign %s", campaign_id)
    return "emailed"
