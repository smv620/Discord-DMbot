"""The owner offers a campaign by email, from the account page (#2532; docs/PLAN.md, "Hand-over
by email"). The rules and the email are `dmbot.handover_email`, the same code the bot's form uses.
The reply is "emailed" whatever the address is: the owner never learns whether it has an account or
a plan. Only the campaign's owner, in a server on this session's own list, can offer it.
"""

from __future__ import annotations

import re

from dmbot import handover_email
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.mail import Mailer
from dmbot.web.sessions import Session

_REF = re.compile(r"(\d{1,19})\.([0-9a-f]{32})")  # the campaign's address, `<server>.<campaign>`


async def offer(
    db: Database,
    session: Session,
    mailer: Mailer | None,
    ref: str,
    raw_email: object,
    *,
    site_url: str,
    now: int,
) -> handover_email.Outcome | None:
    """The outcome, or None when the campaign is not one the signed-in person can offer (unknown,
    another server, not theirs): the same as "not found"."""
    match = _REF.fullmatch(ref)
    if match is None:
        return None
    guild_id, campaign_id = int(match[1]), match[2]
    guild = next((g for g in session.guilds if int(g.id) == guild_id), None)
    if guild is None:
        return None
    person = db.as_person(session.user_id, session.id_hash)
    store = CampaignStore(person, clock=lambda: now)
    campaign = await store.get(guild_id, campaign_id)
    if campaign is None or campaign.owner_user_id != session.user_id:
        return None
    return await handover_email.offer_by_email(
        person,
        store,
        mailer,
        owner_id=session.user_id,
        owner_name=session.display_name,
        guild_id=guild_id,
        campaign_id=campaign_id,
        campaign_name=campaign.name,
        server_name=guild.name,
        raw_email=raw_email,
        site_url=site_url,
        now=now,
    )
