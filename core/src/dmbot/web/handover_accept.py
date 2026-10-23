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

import logging
import re
from typing import Any, Literal

from dmbot import campaign_cap
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.web.sessions import Session, hash_token

log = logging.getLogger(__name__)

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


async def _release(db: Database, session: Session, link: tuple[int, int, str]) -> None:
    """Give a claim back after an accept that did not happen, so the link still works for the
    person it was for. Best effort: the claim's holder can also simply try again."""
    guild_id, offer_id, secret = link
    try:
        async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
            await conn.execute(
                "SELECT dmbot_handover_release(%s, %s, %s)",
                (guild_id, offer_id, hash_token(secret)),
            )
    except Exception:  # the type only: an error can quote a row
        log.warning("A hand-over claim could not be given back")


async def look(
    db: Database, session: Session, raw: object, *, now: int, enforce_plans: bool = False
) -> dict[str, Any]:
    """What the page shows. Changes nothing. `{"status": "ended"}` for every link that opens
    nothing now. (`enforce_plans` only decides the campaign cap's source in the store; the paid
    plan rule of a hand-over by email holds either way.)"""
    link = parse_link(raw)
    offer = None if link is None else await _open(db, session, link, claim=False)
    if link is None or offer is None:
        return ENDED
    guild_id = link[0]
    why = await _why_not(db, session, guild_id, offer, now=now)
    # The session's server list is a snapshot from sign-in (not a live check that the person is
    # still in the server), as for every other answer on the website (web/offers.py).
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


def _store(db: Database, session: Session, now: int) -> CampaignStore:
    return CampaignStore(
        db.as_person(session.user_id, session.id_hash),
        clock=lambda: now,
        has_free_slot=campaign_cap.has_paid_room_for_one_more,
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
    """Accept as the signed-in account. (outcome, the campaign's address when accepted.)

    The order keeps the link usable for the person it was meant for: everything that can be
    checked is checked before the link is claimed, and if the accept still does not happen (a
    race on the person's room, the campaign changing hands) the claim is given back."""
    link = parse_link(raw)
    if link is None:
        return "ended", None
    guild_id, offer_id, _secret = link
    if str(server_id) != str(guild_id) or guild_id not in {g.id for g in session.guilds}:
        return "bad_server", None  # slice C: another server
    offer = await _open(db, session, link, claim=False)
    if offer is None:
        return "ended", None
    why = await _why_not(db, session, guild_id, offer, now=now)
    if why is not None:
        return why, None
    if await _open(db, session, link, claim=True) is None:
        return "ended", None
    try:
        result = await _store(db, session, now).accept_handover(
            guild_id, offer_id, session.user_id, now, announce=True
        )
    except BaseException:
        await _release(db, session, link)
        raise
    if result == "accepted":
        return "accepted", f"{guild_id}.{offer['campaign_id']}"
    await _release(db, session, link)
    return ("no_slot" if result == "no_free_slot" else "ended"), None


async def decline(
    db: Database, session: Session, raw: object, *, now: int, enforce_plans: bool = False
) -> Outcome:
    """Say no thanks. Whoever holds the link is its recipient (that is what an emailed one-use
    link is), so they can end the offer; the owner is told "The person you emailed" unless the
    person has signed in with a name. Only a person in the server can record it (the website's
    rights are held to the session's servers); anyone else just leaves it: the offer ends by
    itself after 7 days."""
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
    result = await _store(db, session, now).decline_handover(
        guild_id, offer_id, session.user_id, now, announce=True
    )
    return "declined" if result == "declined" else "ended"
