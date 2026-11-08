"""🔊 Hear it on the Names page (#3807): a name played in the recap narrator's voice.

The website's container is never given the voice company's key (#2419), so a press only queues a
row (`dmbot_web_hear_add`, 0387) that the bot answers with the narrator's voice (dm_screen/
said_as_hears.py), and the page polls the row (`dmbot_web_hear_read`) for the clip. Here: the
checks the web side can make before queuing, and the poll's answer.

Who may press: a DM of the campaign, for a name on its Names list (a secret name, another
campaign's and a person are "not found", as for the save). The words are the box's current text,
possibly unsaved, held to the same plain-letters rule as a suggestion and 80 characters. Nothing is
queued ("voice_off") when narration is not set up on this server or the campaign owner's plan has
none: the page then falls back to the browser's own voice and says so. The words are never logged.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from dmbot import plan_rules, progress
from dmbot.db import Database
from dmbot.web.names import _SPELLING, SAID_AS_MAX, _alias_id, _parse, list_names
from dmbot.web.recap_choices import owner_access
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


def clean_text(text: object) -> str:
    """The words to speak: tidy spaces, plain letters joined by hyphens, spaces or apostrophes,
    at most 80 characters. Anything else is `bad_text`."""
    if not isinstance(text, str):
        raise RecapError("bad_text")
    clean = unicodedata.normalize("NFC", " ".join(text.split()))
    if not clean or len(clean) > SAID_AS_MAX or not _SPELLING.fullmatch(clean):
        raise RecapError("bad_text")
    return clean


async def queue_hear(
    db: Database,
    session: Session,
    ref: str,
    alias_id: str,
    text: object,
    *,
    audio_on: bool,
    now: int,
    enforce_plans: bool,
) -> str:
    """Queue one press and return its id. Refusals: `not_found` (not a DM, or not a listed name),
    `bad_text`, `voice_off` and `too_soon` (the door's 20 a minute for the campaign)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    alias = _alias_id(alias_id)
    # The DM check and the name check come first, so a stranger learns nothing from the others.
    if not any(n.get("id") == alias for n in await list_names(db, session, ref)):
        raise RecapError("not_found")
    words = clean_text(text)
    if not audio_on:
        raise RecapError("voice_off")
    if enforce_plans:
        access = await owner_access(db, session, guild_id, campaign_id, now)
        if not plan_rules.allowed(plan_rules.RULE_OF["recap_audio"], access):
            raise RecapError("voice_off")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_hear_add(%s, %s, %s) AS r", (guild_id, campaign_id, words)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_hear(db: Database, session: Session, ref: str, press: str) -> dict[str, Any]:
    """Where one of the caller's own presses stands: `state` (waiting, working, done, failed), the
    `error` word when it failed, the shared `progress`, and once, when done, the clip as base64 in
    `audio` (`mime` says what it is). A second read of a done press has no `audio`: the page
    keeps the clip."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.match(press):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_hear_read(%s, %s, %s) AS r", (guild_id, campaign_id, press)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    created = int(got["createdAt"])
    taken = int(got["claimedAt"]) // 1000  # the queue's own stamp is milliseconds
    state = str(got["state"])
    stage = {"waiting": "waiting", "working": "working", "done": "done"}.get(state, "failed")
    out: dict[str, Any] = {
        "state": state,
        "error": got["error"],
        "progress": progress.shape(
            stage,
            started_at=created,
            updated_at=taken if state != "waiting" and taken else created,
            now=int(got["now"]),
            reason=(str(got["error"]) or "hear_failed") if stage == "failed" else None,
        ),
    }
    if got.get("audio"):
        out["audio"] = got["audio"]
        out["mime"] = "audio/wav"
    return out
