"""Deleting a campaign from the website, by its owner, in any server (#2554, part 1).

The website's role sees a server only when it is in the session's own list. An owner whose server
is gone, or whose bot was removed, would otherwise have a campaign they cannot see or delete. A
database door (`dmbot_web_delete_campaign`, schema.py; `GET /me` lists such campaigns through
`dmbot_web_owned_campaigns`) is keyed by the signed-in account, and the delete follows the same
rule as Discord's "Delete this campaign": the owner (with no owner yet, any game master who can
reach the server), the typed name, and not during a session. A campaign is named by
`<server>.<campaign>`, as for the other pages.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.campaigns.models import name_key
from dmbot.db import Database
from dmbot.recaps.audio.files import AudioFiles
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

log = logging.getLogger(__name__)

REFUSALS = {"not_found", "not_the_owner", "session_running", "name_mismatch"}


async def delete(
    db: Database,
    session: Session,
    ref: str,
    body: object,
    *,
    audio: AudioFiles | None,
) -> dict[str, Any]:
    """Delete the campaign for its owner. Raises `RecapError` with the refusal word. The recap
    audio is removed by the bot's sweep (this container cannot); Discord's channels are left (the
    website cannot tidy them), which the answer says."""
    guild_id, campaign_id = _parse(ref)
    typed = body.get("name") if isinstance(body, dict) else None
    if not isinstance(typed, str) or not 0 < len(typed) <= 200:
        raise RecapError("name_mismatch")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_delete_campaign(%s, %s, %s) AS r",
            (guild_id, campaign_id, name_key(typed)),
        )
        row = await cur.fetchone()
    answer = row["r"] if row else None
    status = answer.get("status") if isinstance(answer, dict) else None
    if status != "deleted" or not isinstance(answer, dict):
        raise RecapError(status if status in REFUSALS else "not_found")
    log.info("Campaign deleted from the website: guild=%s campaign=%s", guild_id, campaign_id)
    if audio is not None:
        # Best effort only: the web container mounts the audio volume read-only, so this removes
        # nothing there. The bot sweeps the folders of campaigns that no longer exist.
        audio.forget_campaign(guild_id, campaign_id)
    return {"status": "deleted", "channelsLeft": bool(answer.get("channels"))}
