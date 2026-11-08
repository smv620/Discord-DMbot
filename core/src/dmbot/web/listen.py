"""🎤 Say it on the Names page (#2316): a clip of the DM saying a name, turned into a respelling.

The website's container does no listening: the speech-sound model runs in a worker the bot starts
(dm_screen/said_as_listens.py). A press queues the clip on a row (`dmbot_web_listen_add`, 0388),
the bot writes the respelling and clears the clip in the same statement, and the page reads the
row (`dmbot_web_listen_read`). The route waits a few seconds for the answer so the page can use it
at once; a slower one comes back as an id to poll.

Who may press: a DM of the campaign, for a name on its Names list (a secret name, another
campaign's and a person are "not found", as for the save). `voice_off` when the switch or the
narrator's plan is off: the page then says to type it. The clip is never logged or stored by this
module; the log holds nothing from it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from dmbot import plan_rules, progress
from dmbot.db import Database
from dmbot.web.names import _alias_id, _parse, list_names
from dmbot.web.recap_choices import owner_access
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_CLIP_BYTES = 1_000_000  # the issue's 1 MB (the column allows 1 MiB)
WAIT_S = 20.0  # how long the route waits for the bot before handing back an id to poll
POLL_S = 0.5
POLL_MAX_S = 2.0  # the poll slows down while the bot is busy: fewer reads of the same row
_ID = re.compile(r"^[0-9a-f]{32}$")

# The page's recorder gives webm or ogg; a wav file is read too. The type's parameters
# ("; codecs=opus") are ignored.
CONTAINERS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}


def container_of(content_type: str | None) -> str | None:
    return CONTAINERS.get((content_type or "").split(";")[0].strip().lower())


async def queue_listen(
    db: Database,
    session: Session,
    ref: str,
    alias_id: str,
    clip: bytes,
    content_type: str | None,
    *,
    voice_on: bool,
    now: int,
    enforce_plans: bool,
) -> str:
    """Queue one clip and return its id. Refusals: `not_found` (not a DM, or not a listed name),
    `voice_off`, `bad_clip` (empty, or not a kind of audio read) and `too_big`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    alias = _alias_id(alias_id)
    # The DM check and the name check come first, so a stranger learns nothing from the others.
    if not any(n.get("id") == alias for n in await list_names(db, session, ref)):
        raise RecapError("not_found")
    if not voice_on:
        raise RecapError("voice_off")
    if enforce_plans:
        access = await owner_access(db, session, guild_id, campaign_id, now)
        if not plan_rules.allowed(plan_rules.RULE_OF["recap_audio"], access):
            raise RecapError("voice_off")
    container = container_of(content_type)
    if container is None or not clip:
        raise RecapError("bad_clip")
    if len(clip) > MAX_CLIP_BYTES:
        raise RecapError("too_big")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_listen_add(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, container, clip),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_listen(db: Database, session: Session, ref: str, heard: str) -> dict[str, Any]:
    """Where one of the caller's own clips stands: `state` (waiting, working, done, failed), the
    `error` word when it failed, the shared `progress`, and when done the `respelling` and the
    speech sounds (`ipa`) it was made from. Never the clip."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.match(heard):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_listen_read(%s, %s, %s) AS r", (guild_id, campaign_id, heard)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    created = int(got["createdAt"])
    taken = int(got["claimedAt"]) // 1000  # the queue's own stamp is milliseconds
    state = str(got["state"])
    stage = {"waiting": "waiting", "working": "working", "done": "done"}.get(state, "failed")
    return {
        "state": state,
        "error": got["error"],
        "respelling": got["respelling"],
        "ipa": got["ipa"],
        "progress": progress.shape(
            stage,
            started_at=created,
            updated_at=taken if state != "waiting" and taken else created,
            now=int(got["now"]),
            reason=(str(got["error"]) or "listen_failed") if stage == "failed" else None,
        ),
    }


async def wait_for_answer(
    read: Callable[[], Awaitable[dict[str, Any]]],
    *,
    wait_s: float = WAIT_S,
    poll_s: float = POLL_S,
) -> dict[str, Any] | None:
    """Read the row until it is done or failed, or None after `wait_s` (the caller then hands the
    page an id to poll). One short read at a time: the connection is never held between reads."""
    loop = asyncio.get_running_loop()
    until = loop.time() + wait_s
    while True:
        got = await read()
        if got["state"] in ("done", "failed"):
            return got
        if loop.time() >= until:
            return None
        await asyncio.sleep(poll_s)
        poll_s = min(poll_s * 1.5, max(poll_s, POLL_MAX_S))
