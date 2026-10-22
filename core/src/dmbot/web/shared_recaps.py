"""A posted recap's public, read-only link (#2118): `GET /recaps/{token}` and its audio.

Anyone with the link can read the recap, no sign-in. The token is the whole key (256 random bits,
made when a DM turns the link on, `recaps.share_recap`); it finds one recap and nothing else about
its campaign or server. The website's database role reads it through one door
(`dmbot_recap_shared`), which answers only for a posted recap whose link is on.

What is public is the table version only: the title and the story with its cues removed. Never
the facts still to be confirmed, the numbers-and-items list, a footer or who was at the table.
Before every answer the text is checked for the campaign's secret names again, as at Post; if it
fails (or the names cannot be read), the answer is the same "not found" as for a wrong token, so
nothing tells a turned-off link from a link that never was. The audio is served from the bot's
folder by the same token, never by a path from the request.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dmbot.db import Database
from dmbot.recaps.approval import text_hash
from dmbot.recaps.audio.files import MUSIC, VOICE, AudioFiles
from dmbot.recaps.text import names_a_secret, strip_cues

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
PUBLIC_KINDS = (VOICE, MUSIC)  # never the mix: it is the DM's own copy


async def _find(db: Database, token: str) -> dict[str, Any] | None:
    """The posted recap this token names whose text passes the secret check, else None."""
    if not _TOKEN.fullmatch(token):
        return None
    async with db.anonymous() as conn:
        cur = await conn.execute("SELECT dmbot_recap_shared(%s) AS r", (token,))
        found = (await cur.fetchone() or {}).get("r")
    if found is None:
        return None
    text = "\n".join([found["title"], strip_cues(found["summary"])])
    if names_a_secret(text, found["secret"], found["allowed"]):
        return None
    return dict(found)


def _audio_ok(found: dict[str, Any]) -> bool:
    """The audio may be offered only while it is the audio of this very text: made, with audio
    chosen, and from the title and story as they are now (an edit or a leftover file is not)."""
    return (
        found["audioOn"]  # the campaign still has narrated recaps on
        and found["audio"] == "ready"
        and found["audioChoice"] in ("voice", "music", "both")
        and found["audioHash"] == text_hash(found["title"], found["summary"])
    )


async def read_shared(
    db: Database, files: AudioFiles | None, token: str, api_url: str
) -> dict[str, Any] | None:
    """The public page's data, or None (one answer for every way it can fail)."""
    found = await _find(db, token)
    if found is None:
        return None
    audio: dict[str, str] = {}
    if files is not None and _audio_ok(found):
        try:
            present = files.present(int(found["guild"]), found["campaign"], int(found["at"]))
        except ValueError:
            present = {}
        audio = {
            kind: f"{api_url.rstrip('/')}/recaps/{token}/audio/{kind}"
            for kind in PUBLIC_KINDS
            if kind in present
        }
    return {
        "title": found["title"],
        "text": strip_cues(found["summary"]),
        "postedAt": found["postedAt"],
        # When it was played, and which session it was and how long it ran (#2418): the same facts
        # as the DM's page. The number and length come from the shared door once it carries them;
        # until then the page leaves them out (it must render an answer without them).
        "sessionAt": found.get("at"),
        "sessionNumber": found.get("sessionNumber"),
        "sessionSeconds": found.get("sessionSeconds"),
        "audio": audio,
    }


async def audio_for(db: Database, files: AudioFiles | None, token: str, kind: str) -> Path | None:
    """The audio file a token names (the same checks as the page), or None."""
    if files is None or kind not in PUBLIC_KINDS:
        return None
    found = await _find(db, token)
    if found is None or not _audio_ok(found):
        return None
    try:
        path = files.path(int(found["guild"]), found["campaign"], int(found["at"]), kind)
    except ValueError:
        return None
    return path if path.is_file() else None
