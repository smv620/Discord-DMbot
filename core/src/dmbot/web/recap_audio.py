"""A DM's recap audio on the website (#1418): the signed links to the files, and the file route.

The bot makes the audio and keeps it on its disk (`dmbot.recaps.audio.files`); the web API only
reads it, from a read-only mount. The signed-in DM of the campaign is the only one who is handed a
link: the page asks `GET /me/recaps/<ref>/audio`, which reads the recap under the website's
database rule (a player, or a DM of another campaign, gets "not found"), and answers with links
that stop working after 15 minutes. The file route itself checks the signature and nothing else, so
an <audio> tag, which cannot send our custom request header, can play it. A request names only a
recap and one of three fixed kinds; the path is built by `AudioFiles`, never taken from the request.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from dmbot.recaps.audio import parts
from dmbot.recaps.audio import script as audio_script
from dmbot.recaps.audio.files import KINDS, AudioFiles
from dmbot.recaps.notify import parse_ref

_HEX = re.compile(r"^[0-9a-f]{64}$")


def link_key(secret_key: bytes) -> bytes:
    """The website's secret, tied to this one use so it signs nothing else (not a sign-in check)."""
    return hmac.new(secret_key, b"recap-audio-links", hashlib.sha256).digest()


def audio_files(directory: str) -> AudioFiles | None:
    """The bot's audio folder as the web API sees it; None when it is not set up."""
    return AudioFiles(Path(directory)) if directory else None


def links(files: AudioFiles | None, secret_key: bytes, api_url: str, ref: str) -> dict[str, str]:
    """One signed link for each file that exists for this recap, by kind. Empty if none do."""
    parsed = parse_ref(ref)
    if files is None or parsed is None:
        return {}
    guild_id, campaign_id, session_at = parsed
    try:
        present = files.present(guild_id, campaign_id, session_at)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for kind in KINDS:
        if kind not in present:
            continue
        expires, signature = files.sign(
            link_key(secret_key), guild_id, campaign_id, session_at, kind
        )
        query = urlencode({"e": expires, "s": signature})
        out[kind] = f"{api_url.rstrip('/')}/recap-audio/{ref}/{kind}?{query}"
    return out


def file_for(
    files: AudioFiles | None,
    secret_key: bytes,
    ref: str,
    kind: str,
    expires: str,
    signature: str,
) -> Path | None:
    """The file a genuine, unexpired link points at; None for anything else (one answer for a
    wrong signature, an old link, a bad kind and a missing file, so nothing is learned from it)."""
    # Plain ASCII only, checked here before anything is parsed or compared: `str.isdigit` is true
    # for characters `int` refuses, and comparing a non-ASCII signature raises. Both would be
    # a 500 on a route anyone can call.
    if not (ref.isascii() and expires.isascii() and expires.isdecimal() and len(expires) <= 12):
        return None
    if not _HEX.match(signature):
        return None
    parsed = parse_ref(ref)
    if files is None or parsed is None:
        return None
    guild_id, campaign_id, session_at = parsed
    return files.verify(
        link_key(secret_key), guild_id, campaign_id, session_at, kind, int(expires), signature
    )


def remake(
    files: AudioFiles | None, ref: str, recap: dict[str, Any], *, voice_tags: bool
) -> dict[str, Any]:
    """What "Make it again" would do for this recap (#2630), so the page can say which button it
    is and why: `how` is `voice` (only the `changed` paragraphs of `total` are spoken again, the
    music kept), `all` (everything again; `why` is paragraphs, moods, size or no_parts), `same` (the
    words the voice reads have not changed) or `new` (nothing was made before). Worked out from
    the kept manifest and the recap's text as it is now, with the same rules the bot applies when
    it makes the audio; only fixed words and counts leave here."""
    audio = recap["audio"]
    out: dict[str, Any] = {"how": "new", "why": "", "changed": 0, "total": 0}
    parsed = parse_ref(ref)
    if files is None or parsed is None or audio["state"] not in ("ready", "failed"):
        return out
    script = audio_script.build(
        recap["title"], recap["gameDay"], recap["summary"], emotion_tags=voice_tags
    )
    out["total"] = len(script.paragraphs)
    if audio["current"]:
        return {**out, "how": "all"}  # the same text: a new take, all of it
    guild_id, campaign_id, session_at = parsed
    try:
        folder = files.recap_dir(guild_id, campaign_id, session_at)
    except ValueError:
        return out
    manifest = parts.read_manifest(folder)
    # (the mode is the one the audio was made in; the bot checks the DM's choice again when it
    # makes it, and a different choice makes everything again)
    choice = recap["approval"]["choice"] or (manifest or {}).get("mode")
    if manifest is None or choice not in ("voice", "both") or choice != manifest.get("mode"):
        return {**out, "how": "all", "why": parts.NO_PARTS}
    change = parts.classify(manifest, script, mode=choice, seed=session_at)
    return {**out, "how": change.kind, "why": change.why, "changed": len(change.changed)}
