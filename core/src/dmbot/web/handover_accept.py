"""Opening, accepting and declining a hand-over link on the website (#2532 slice B; docs/PLAN.md,
"Hand-over by email"). The link is `/handover#<server>-<offer>-<secret>`: the offer holds only a
SHA-256 of the secret, and the database's door `dmbot_handover_open` reads that one offer. Nothing
here says why a link has ended: unknown, expired, taken back, used by someone else and "the owner
changed" all read the same, so a link tells a stranger nothing. Accepting is the campaign
store's own `accept_handover` for the signed-in DMbot account (paid plan, room for one more, not
the owner), the same code the Discord buttons and the account page use, so ownership moves to the
account.

This slice moves a campaign only within the server it runs on now; another server is slice C.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from dmbot import campaign_cap
from dmbot.campaigns.store import CampaignStore, plan_works
from dmbot.db import Database
from dmbot.web.sessions import Session, hash_token

Why = Literal["no_plan", "no_slot", "owner"]
Outcome = Literal["accepted", "declined", "ended", "no_plan", "no_slot", "owner", "bad_server"]

_LINK = re.compile(r"(\d{1,19})-(\d{1,19})-([A-Za-z0-9_-]{20,128})")  # BIGINT holds 19 digits
ENDED: dict[str, Any] = {"status": "ended"}


def parse_link(raw: object) -> tuple[int, int, str] | None:
    """(server, offer, secret) from what the page sends, or None for anything else."""
    match = _LINK.fullmatch(raw) if isinstance(raw, str) else None
    return None if match is None else (int(match[1]), int(match[2]), match[3])


async def _open(
    db: Database, session: Session, link: tuple[int, int, str], *, claim: bool
) -> dict[str, Any] | None:
    """The offer the link opens (and claims it for this person when `claim`), or None."""
    guild_id, offer_id, secret = link
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_open(%s, %s, %s, %s, %s) AS offer",
            (guild_id, offer_id, hash_token(secret), session.display_name, claim),
        )
        row = await cur.fetchone()
    offer = row["offer"] if row else None
    return offer if isinstance(offer, dict) else None


async def _why_not(
    db: Database, session: Session, guild_id: int, offer: dict[str, Any], *, now: int
) -> Why | None:
    if offer["from_user_id"] == session.user_id:
        return "owner"
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        verdict = await campaign_cap.handover_verdict(conn, session.user_id, now)
    return None if verdict == "ok" else verdict


async def look(
    db: Database, session: Session, raw: object, *, now: int, enforce_plans: bool = False
) -> dict[str, Any]:
    """What the page shows. Changes nothing. `{"status": "ended"}` for every link that opens
    nothing now."""
    link = parse_link(raw)
    offer = None if link is None else await _open(db, session, link, claim=False)
    if link is None or offer is None:
        return ENDED
    guild_id = link[0]
    why = await _why_not(db, session, guild_id, offer, now=now) if enforce_plans else None
    if why is None and offer["from_user_id"] == session.user_id:
        why = "owner"
    mine = next((g for g in session.guilds if g.id == guild_id), None)
    server = {"id": str(guild_id), "name": mine.name if mine else ""}
    return {
        "status": "open",
        "campaign": {"name": offer["campaign_name"]},
        "from": {"name": offer["from_name"]},
        "server": server,
        "canAccept": why is None and mine is not None,
        "why": why,
        # Slice B: only the server it runs on now, and only when the person is in it (C adds more).
        "servers": []
        if mine is None
        else [{"id": str(guild_id), "name": mine.name, "current": True, "botIn": True}],
    }


def _store(db: Database, session: Session, now: int, enforce_plans: bool) -> CampaignStore:
    return CampaignStore(
        db.as_person(session.user_id, session.id_hash),
        clock=lambda: now,
        has_free_slot=campaign_cap.has_paid_room_for_one_more if enforce_plans else plan_works,
    )


async def accept(
    db: Database,
    session: Session,
    raw: object,
    server_id: object,
    *,
    now: int,
    enforce_plans: bool = False,
) -> tuple[Outcome, str | None]:
    """Accept as the signed-in account. (outcome, the campaign's address when accepted.)"""
    link = parse_link(raw)
    if link is None:
        return "ended", None
    guild_id, offer_id, _secret = link
    if str(server_id) != str(guild_id) or guild_id not in {g.id for g in session.guilds}:
        return "bad_server", None  # slice C: another server
    offer = await _open(db, session, link, claim=False)
    if offer is None:
        return "ended", None
    if enforce_plans:
        why = await _why_not(db, session, guild_id, offer, now=now)
        if why is not None:
            return why, None
    elif offer["from_user_id"] == session.user_id:
        return "owner", None
    if await _open(db, session, link, claim=True) is None:
        return "ended", None
    result = await _store(db, session, now, enforce_plans).accept_handover(
        guild_id, offer_id, session.user_id, now, announce=True
    )
    if result == "accepted":
        return "accepted", f"{guild_id}.{offer['campaign_id']}"
    return ("no_slot" if result == "no_free_slot" else "ended"), None


async def decline(
    db: Database, session: Session, raw: object, *, now: int, enforce_plans: bool = False
) -> Outcome:
    """Say no thanks. Only a person in the server can record it (the website's rights are held to
    the session's servers); anyone else just leaves it: the offer ends by itself after 7 days."""
    link = parse_link(raw)
    if link is None:
        return "ended"
    guild_id, offer_id, _secret = link
    offer = await _open(db, session, link, claim=False)
    if offer is None or offer["from_user_id"] == session.user_id:
        return "ended"
    if guild_id not in {g.id for g in session.guilds}:
        return "declined"
    if await _open(db, session, link, claim=True) is None:
        return "ended"
    result = await _store(db, session, now, enforce_plans).decline_handover(
        guild_id, offer_id, session.user_id, now, announce=True
    )
    return "declined" if result == "declined" else "ended"
