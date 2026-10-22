"""The DM's three recap choices on the website (#1468), the same as the DM screen's 📜 Recaps panel
(`dm_screen/recap_panel.py`, #1421): write a recap after each session, make the narration and
music, email me when it is ready.

They live in `campaigns.recaps_text`, `recaps_audio` and `recaps_email`. The website's role cannot
write that table, so both the read and the write go through two database doors
(`dmbot_web_recap_choices`, `dmbot_web_set_recap_choice`, schema.py) that answer only a DM of the
campaign, never a server manager, exactly as the panel does.

The choices can only turn things off in effect (the bot's plan gate decides at each recap and
fails closed), so the one plan question the page needs is whether to offer the narration switch at
all, and whether turning it on is allowed. The bot reads the campaign **owner's** plan, and so does
this (#1470): every DM sees the same switch the DM screen shows, owner or co-DM. The owner's id
comes only from the DM-checked door, never from the browser, and only two facts about the plan
leave this module (whether the narration is in it, and how); never the plan's name, its payment
state or the owner's id.
A campaign is named by `<server>.<campaign>`, the recap's address without its last part.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.entitlements import RECAP_AUDIO_PLANS, plan_grant_access
from dmbot.recaps.allowance import month_bounds
from dmbot.recaps.notify import parse_ref
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

WHICH = ("text", "audio", "email")


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")  # the recap address's own checks: ASCII, bounded
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


async def _audio_facts(
    db: Database, owner: str | None, now: int, *, enforce_plans: bool
) -> dict[str, Any]:
    """Whether the narration switch is offered and may be turned on, and how the plan has it
    (`recap_panel.audio_how`'s two answers), from the campaign owner's plan. With
    plans not enforced (the test server) the bot allows it, so the page does too."""
    if not enforce_plans:
        return {"canAudio": True, "audioHow": "", "limited": False}
    if owner is None or not owner.isascii() or not owner.isdecimal():
        return {"canAudio": False, "audioHow": "", "limited": True}
    # The owner's plan, as the bot reads it (`recap_plan_facts`), not the signed-in person's.
    _plan, _grant, access = await plan_grant_access(db, int(owner), now)
    if not (access.works and access.recap_audio):  # the bot's `can_recap_audio`
        return {"canAudio": False, "audioHow": "", "limited": True}
    # As the panel says it: "part of your plan" unless it is a paid plan outside Guild and Pro.
    how = (
        "an add-on"
        if access.kind == "paid" and (access.plan_name or "").casefold() not in RECAP_AUDIO_PLANS
        else "part of your plan"
    )
    # Free access and grants are not held to the month's allowance by the bot (`_recap_allowance`).
    return {"canAudio": True, "audioHow": how, "limited": access.kind not in ("free", "grant")}


async def read_choices(
    db: Database,
    session: Session,
    ref: str,
    *,
    now: int,
    enforce_plans: bool,
    recaps_on: bool = True,
    audio_allowed: int | None = None,
) -> dict[str, Any]:
    """The three choices and whether the narration may be offered. A player, another campaign's DM
    and a server manager who is no DM get "not found". `audioUnavailable` says why narration can't
    be made right now, for the recap page's greyed button (#2419): "not_set_up" (`recaps_on` is
    false: nothing on this server makes it), "plan" (the owner's plan has none) or "allowance" (the
    month's narrated recaps are used up, `audio_allowed`, for a campaign the bot holds to it);
    empty when it can be made. It is advice for the page: the bot checks again before any paid
    call."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_recap_choices(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    facts = await _audio_facts(db, found.get("owner"), now, enforce_plans=enforce_plans)
    limited = bool(facts.pop("limited"))
    why = ""
    if not recaps_on:
        why = "not_set_up"
    elif not facts["canAudio"]:
        why = "plan"
    elif limited and audio_allowed is not None:
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            if await _narrated_this_month(conn, guild_id, campaign_id, now) >= audio_allowed:
                why = "allowance"
    return {
        "text": bool(found["text"]),
        "email": bool(found["email"]),
        "audio": bool(found["audio"]),
        **facts,
        "audioUnavailable": why,
    }


async def _narrated_this_month(conn: Any, guild_id: int, campaign_id: str, now: float) -> int:
    """The campaign's narrated recaps this month, counted as the bot counts them
    (`RecapStore.used_in_month`): each made one once and each Try again again."""
    start, end = month_bounds(now)
    cur = await conn.execute(
        "SELECT COALESCE(sum((CASE WHEN audio = 'ready' THEN 1 ELSE 0 END)"
        " + (CASE WHEN audio <> 'off' THEN tries ELSE 0 END)), 0) AS n FROM recap_jobs"
        " WHERE guild_id = %s AND campaign_id = %s AND created_at >= %s AND created_at < %s",
        (guild_id, campaign_id, start, end),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def set_choice(
    db: Database, session: Session, ref: str, which: str, on: bool, *, now: int, enforce_plans: bool
) -> dict[str, Any]:
    """Change one choice; returns all three as they are now. Turning the narration on needs a plan
    that has it ("not_in_plan"); turning anything off never does."""
    if which not in WHICH:
        raise RecapError("bad_choice")
    current = await read_choices(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )  # also the DM check, before any write
    if which == "audio" and on and not current["canAudio"]:
        raise RecapError("not_in_plan")
    guild_id, campaign_id = _parse(ref)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_set_recap_choice(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, which, bool(on)),
        )
        row = await cur.fetchone()
    outcome = row["r"] if row else "not_allowed"
    if outcome != "ok":
        raise RecapError("bad_choice" if outcome == "bad_choice" else "not_found")
    after = await read_choices(db, session, ref, now=now, enforce_plans=enforce_plans)
    after.pop("audioUnavailable")  # not asked here (no allowance or set-up facts): never half-said
    return after
