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

# Slice C (#2532): the person may choose another of their servers. The website only records the
# wish (`dmbot_handover_request_move`); the bot's `campaigns.moves.HandoverMover` does the move.
# Kept as a switch so a build without the mover can never accept a move that would wait for ever.
MOVES_ARE_RUN = True

Why = Literal["no_plan", "try_it", "no_slot", "owner", "shares_rules"]
Outcome = Literal[
    "accepted",
    "moving",
    "declined",
    "ended",
    "no_plan",
    "try_it",
    "no_slot",
    "owner",
    "bad_server",
    "shares_rules",
]

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


async def _shares_rules(db: Database, session: Session, link: tuple[int, int, str]) -> bool:
    """Does the campaign share a rule set? Then it cannot move to another server yet (#2551); it
    can still be handed over on this server."""
    guild_id, offer_id, secret = link
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_shares_rules(%s, %s, %s) AS yes",
            (guild_id, offer_id, hash_token(secret)),
        )
        row = await cur.fetchone()
    return bool(row and row["yes"])


async def _move_failed(db: Database, session: Session, link: tuple[int, int, str]) -> str | None:
    """Why the move the signed-in person asked for last time did not happen (theirs to see only)."""
    guild_id, offer_id, secret = link
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_move_failed(%s, %s, %s) AS why",
            (guild_id, offer_id, hash_token(secret)),
        )
        row = await cur.fetchone()
    return str(row["why"]) if row and row["why"] else None


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


MAX_SERVERS = 25  # asked about on one page; a person with more picks from the first ones


async def _bot_in(db: Database, session: Session, guild_id: int) -> bool:
    """Is DMbot installed in this server (a server of the session's own list)?"""
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = %s AND left_at IS NULL) AS yes",
            (guild_id,),
        )
        row = await cur.fetchone()
    return bool(row and row["yes"])


MOVE_REFUSED = "can't move to another server yet, but it can be handed over on this server"


async def _servers(
    db: Database, session: Session, current: int, *, sharing: bool = False
) -> list[dict[str, Any]]:
    """Where the campaign could run: the server it runs on now (when the person is in it), and,
    once the bot's move exists (`MOVES_ARE_RUN`), their other servers with whether DMbot is in
    each ("Add DMbot to a server" for the rest). The server list is a snapshot from sign-in, not a
    live check (as for every other answer on the website, web/offers.py)."""
    out: list[dict[str, Any]] = []
    for g in session.guilds[:MAX_SERVERS]:
        if g.id == current:
            out.append({"id": str(g.id), "name": g.name, "current": True, "botIn": True})
        elif MOVES_ARE_RUN and g.manage:
            out.append(
                {
                    "id": str(g.id),
                    "name": g.name,
                    "current": False,
                    "botIn": await _bot_in(db, session, g.id),
                    # Not a choice while the campaign shares a rule set (#2551): said up front.
                    "why": MOVE_REFUSED if sharing else None,
                }
            )
    return sorted(out, key=lambda s: not s["current"])


async def look(db: Database, session: Session, raw: object, *, now: int) -> dict[str, Any]:
    """What the page shows. Changes nothing. `{"status": "ended"}` for every link that opens
    nothing now."""
    link = parse_link(raw)
    offer = None if link is None else await _open(db, session, link, claim=False)
    if link is None or offer is None:
        return ENDED
    guild_id = link[0]
    why = await _why_not(db, session, guild_id, offer, now=now)
    servers = await _servers(db, session, guild_id, sharing=await _shares_rules(db, session, link))
    mine = next((g for g in session.guilds if g.id == guild_id), None)
    return {
        "status": "open",
        "campaign": {"name": offer["campaign_name"]},
        "from": {"name": offer["from_name"]},
        "server": {"id": str(guild_id), "name": mine.name if mine else ""},
        "canAccept": why is None and any(s["current"] or s["botIn"] for s in servers),
        "why": why,
        "moveFailed": await _move_failed(db, session, link),  # the last try, for this person
        "servers": servers,
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
) -> tuple[Outcome, str | None]:
    """Accept as the signed-in account. (outcome, the campaign's address when accepted.)

    The order keeps the link usable for the person it was meant for: everything that can be
    checked is checked before the link is claimed, and if the accept still does not happen (a
    race on the person's room, the campaign changing hands) the claim is given back."""
    link = parse_link(raw)
    if link is None:
        return "ended", None
    guild_id, offer_id, _secret = link
    target = _as_server(server_id)
    mine = {g.id for g in session.guilds}
    managed = {g.id for g in session.guilds if g.manage}
    elsewhere = target is not None and target != guild_id
    if target is None or target not in mine or (elsewhere and not MOVES_ARE_RUN):
        return "bad_server", None
    if elsewhere and target not in managed:  # the page lists only servers the person manages
        return "bad_server", None
    offer = await _open(db, session, link, claim=False)
    if offer is None:
        return "ended", None
    why = await _why_not(db, session, guild_id, offer, now=now)
    if why is not None:
        return why, None
    if elsewhere:
        if await _shares_rules(db, session, link):
            return "shares_rules", None  # refused before they accept or buy a plan
        return await _ask_for_move(db, session, link, target), None
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


def _as_server(value: object) -> int | None:
    text = str(value)
    return int(text) if text.isdigit() and len(text) <= 19 else None


async def _ask_for_move(
    db: Database,
    session: Session,
    link: tuple[int, int, str],
    target: int | None,
) -> Outcome:
    """Claim the link and record that the person wants the campaign in `target` (one database
    step): the bot moves it (slice C2). "bad_server" when that server is not one of theirs with
    DMbot installed; "ended" when the link opens nothing now."""
    guild_id, offer_id, secret = link
    assert target is not None
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_request_move(%s, %s, %s, %s, %s) AS ok",
            (guild_id, offer_id, hash_token(secret), session.display_name, target),
        )
        row = await cur.fetchone()
    if row and row["ok"]:
        return "moving"
    # The same "no" for every reason would hide a missing install: say which when the link is fine.
    if await _open(db, session, link, claim=False) is None:
        return "ended"
    return "bad_server"


async def decline(db: Database, session: Session, raw: object) -> Outcome:
    """Say no thanks. Whoever holds the link is its recipient (that is what an emailed one-use
    link is), so they can end the offer, wherever they are: one database door records it under
    their id and name (the website's role can write only in the servers of its session, and the
    person emailed is often not in the campaign's server) and the bot tells the owner."""
    link = parse_link(raw)
    if link is None:
        return "ended"
    guild_id, offer_id, secret = link
    async with db.as_person(session.user_id, session.id_hash).guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_handover_decline(%s, %s, %s, %s) AS ok",
            (guild_id, offer_id, hash_token(secret), session.display_name),
        )
        row = await cur.fetchone()
    return "declined" if row and row["ok"] else "ended"
