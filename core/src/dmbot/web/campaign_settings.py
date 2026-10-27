"""The website's Settings page, the part that changes things (#2463): a DM's choice for who can see
the DM screen, how much DMbot says, and how long transcripts are kept.

The website's database role has no right on the campaign tables, so a choice is queued through one
database door (`dmbot_web_setting_add`, schema.py), which checks the signed-in person is a DM of the
campaign; the bot applies it the way Discord's ⚙️ Settings buttons do (dm_screen/web_settings.py)
and the page reads where it stands. A server manager who is not a DM, a player and anyone else get
"not allowed" or "not found", the same as for the other pages. Optional rules (#2753) and game
masters (#2768) go through the same queue. Not here: hand over, pause and delete (the page sends the
DM to Discord for those).

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.rules import optional
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# What each setting accepts: the choices Discord offers, nothing else.
CHOICES: dict[str, tuple[str, ...]] = {
    "visibility": ("private", "peek", "open"),
    "level": ("quiet", "normal"),
    "retention": ("forever", "365", "90", "30"),
}


OPTIONAL_RULE = "optional_rule"  # a fourth choice: one optional rule on or off (#2753)
CO_DM = "co_dm"  # a fifth: one game master added or removed (#2768)
PERSON_ID = re.compile(r"^[1-9][0-9]{0,18}$")  # a Discord id: digits, never a name


def _arguments(body: object) -> tuple[str, str, bool]:
    """(setting, value, confirmed) for the database door, or the refusal word."""
    if not isinstance(body, dict):
        raise RecapError("bad_setting")
    setting = body.get("setting")
    if not isinstance(setting, str) or setting not in CHOICES:
        raise RecapError("bad_setting")
    value = body.get("value")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)  # (a page may send 90 for "90 days")
    if not isinstance(value, str) or value not in CHOICES[setting]:
        raise RecapError("bad_value")
    return setting, value, body.get("confirm") is True


def _optional_rule(body: object) -> tuple[str, bool] | None:
    """(rule id, on) when the body is an optional-rule choice (#2753), None when it is one of the
    three settings. The id must be one in the rules catalog, the same list Discord offers, and `on`
    a real yes or no: anything else is the refusal word. The bot checks again that the rule applies
    to this campaign's main rules."""
    if not isinstance(body, dict) or body.get("setting") != OPTIONAL_RULE:
        return None
    rule_id, on = body.get("value"), body.get("on")
    if not isinstance(rule_id, str) or optional.rule(rule_id) is None or not isinstance(on, bool):
        raise RecapError("bad_value")
    return rule_id, on


def _co_dm(body: object) -> tuple[str, bool] | None:
    """(person's id, add) when the body is a game master change (#2768), None otherwise. The id is
    digits only, as a Discord id is, and `on` a real yes or no: anything else is the refusal word.
    The database door checks the person is one the candidates list offers; the bot checks every
    rule of Discord's game masters again when it applies the change."""
    if not isinstance(body, dict) or body.get("setting") != CO_DM:
        return None
    person, add = body.get("value"), body.get("on")
    if not isinstance(person, str) or not PERSON_ID.match(person) or not isinstance(add, bool):
        raise RecapError("bad_value")
    return person, add


async def queue_setting(
    db: Database, session: Session, ref: str, body: object, *, co_dms_on: bool = False
) -> str:
    """Queue one choice of the signed-in DM for the bot and return its id. A server the person is
    not in is "not found"; a member who is not a DM of the campaign is "not allowed". A shorter
    transcript limit deletes old sessions, so the page asks first and sends `confirm`; the bot
    refuses it without."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    rule = _optional_rule(body)
    master = _co_dm(body)
    if master and not co_dms_on:  # off until the bot applies it too (DMBOT_WEB_CO_DMS, #2768)
        raise RecapError("not_found")
    setting, value, confirmed = ("", "", False) if rule or master else _arguments(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        if rule:
            cur = await conn.execute(
                "SELECT dmbot_web_optional_rule_add(%s, %s, %s, %s) AS r",
                (guild_id, campaign_id, rule[0], rule[1]),
            )
        elif master:
            cur = await conn.execute(
                "SELECT dmbot_web_co_dm_add(%s, %s, %s, %s, %s) AS r",
                (guild_id, campaign_id, master[0], master[1], TERMS_VERSION),
            )
        else:
            cur = await conn.execute(
                "SELECT dmbot_web_setting_add(%s, %s, %s, %s, %s) AS r",
                (guild_id, campaign_id, setting, value, confirmed),
            )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_setting(db: Database, session: Session, ref: str, answer: str) -> dict[str, Any]:
    """Where one of the caller's own choices stands: `state` (waiting, working, done, failed), the
    `error` word when it failed, and `note`, the words Discord would say (for a rule the DM can
    fix, or what the change did)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_setting_read(%s, %s, %s) AS r", (guild_id, campaign_id, answer)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {
        "state": got["state"],
        "error": got["error"],
        "setting": got["setting"],
        "value": got["value"],
        "note": got["note"],
    }
