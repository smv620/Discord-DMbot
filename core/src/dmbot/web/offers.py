"""Answering a campaign hand-over on the website (#614, #437): accept, decline, withdraw.

The rules live in the campaign store (`CampaignStore.accept_handover` and the rest), the
same code the bot's Discord buttons call, so the two can't drift apart. Here the store
runs with the signed-in person and session set, so the website's role is held to that
session's servers and to offers the person sent or was sent (#498, schema policies).

The site names an offer by `<server id>-<offer id>`: the server is needed to read the row
at all (every server table is scoped by server), and it must be one in the session's own
Discord list, or the offer is treated as gone.
"""

from __future__ import annotations

import re
from typing import Literal

from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.web.sessions import Session

Answer = Literal["accept", "decline", "withdraw"]
Outcome = Literal["accepted", "declined", "withdrawn", "no_free_slot", "gone"]

_REF = re.compile(r"(\d{1,20})-(\d{1,20})")


def offer_ref(guild_id: int, offer_id: int) -> str:
    """How the site names an offer (the `id` in /me's offer lists)."""
    return f"{guild_id}-{offer_id}"


async def answer(db: Database, session: Session, ref: str, what: Answer, *, now: int) -> Outcome:
    """Answer an offer as the signed-in person. "gone" covers every offer that isn't
    theirs to answer now: unknown, another person's, answered, withdrawn or expired."""
    match = _REF.fullmatch(ref)
    if match is None:
        return "gone"
    guild_id, offer_id = int(match[1]), int(match[2])
    if guild_id not in {g.id for g in session.guilds}:
        return "gone"
    store = CampaignStore(db.as_person(session.user_id, session.id_hash), clock=lambda: now)
    if what == "accept":
        return await store.accept_handover(guild_id, offer_id, session.user_id, now)
    if what == "decline":
        return await store.decline_handover(guild_id, offer_id, session.user_id, now)
    return await store.withdraw_handover(guild_id, offer_id, session.user_id, now)
