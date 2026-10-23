"""Where a recap's audio lives on the server's disk (#1383), and the signed links that fetch it.

Audio is not in Postgres or in backups (it is made again from the text). It sits under the
campaign's own directory, so nothing crosses between campaigns or servers, for 180 days or until
the campaign is deleted. A name taken from a request is checked before it becomes a path: only
the fixed file kinds, and only numbers and the campaign's own id characters, ever reach the disk.

A link is a short-lived signature over the campaign, the session, the kind and the expiry; the
web API (a later change) hands one to the signed-in DM only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

KEEP_DAYS = 180
LINK_SECONDS = 15 * 60
VOICE = "voice"
MUSIC = "music"  # the background: music and ambience together
MIX = "mix"
KINDS = (VOICE, MUSIC, MIX)
FILE_NAMES = {VOICE: "recap-voice.mp3", MUSIC: "recap-music.mp3", MIX: "recap-mix.mp3"}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SCENE = re.compile(r"^[a-z]{1,20}$")


class AudioFiles:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._root = Path(root)
        self._now = clock

    def _campaign(self, guild_id: int, campaign_id: str) -> Path:
        if guild_id <= 0 or not _ID.match(campaign_id):
            raise ValueError("not a campaign")
        return self._root / str(int(guild_id)) / campaign_id

    def recap_dir(self, guild_id: int, campaign_id: str, session_at: int) -> Path:
        if session_at <= 0:
            raise ValueError("not a session")
        return self._campaign(guild_id, campaign_id) / "recaps" / str(int(session_at))

    def path(self, guild_id: int, campaign_id: str, session_at: int, kind: str) -> Path:
        if kind not in FILE_NAMES:
            raise ValueError("not a kind of file")
        return self.recap_dir(guild_id, campaign_id, session_at) / FILE_NAMES[kind]

    def present(self, guild_id: int, campaign_id: str, session_at: int) -> dict[str, Path]:
        """The files that exist for this recap, by kind."""
        return {
            kind: p
            for kind in KINDS
            if (p := self.path(guild_id, campaign_id, session_at, kind)).is_file()
        }

    def ambience_path(self, guild_id: int, campaign_id: str, scene: str) -> Path:
        """A cached ambience loop: one for each campaign and scene kind."""
        if not _SCENE.match(scene):
            raise ValueError("not a scene kind")
        return self._campaign(guild_id, campaign_id) / "ambience" / f"{scene}.mp3"

    def forget_campaign(self, guild_id: int, campaign_id: str) -> None:
        shutil.rmtree(self._campaign(guild_id, campaign_id), ignore_errors=True)

    def sweep_gone(self, keep: Mapping[int, Iterable[str]], *, min_age_s: float = 86400) -> int:
        """Delete the folders of campaigns that no longer exist (#2554): `keep` is every campaign
        that does, by server. The website's delete cannot remove audio (its container mounts the
        volume read-only), so the bot does it here. A folder newer than `min_age_s` stays, so a
        campaign made a moment after `keep` was read is never swept. Returns how many went."""
        gone = 0
        for guild_dir in self._root.glob("*"):
            if not guild_dir.is_dir() or not guild_dir.name.isdigit():
                continue
            alive = set(keep.get(int(guild_dir.name), ()))
            for folder in guild_dir.glob("*"):
                try:
                    if (
                        folder.is_dir()
                        and folder.name not in alive
                        and self._now() - folder.stat().st_mtime >= min_age_s
                    ):
                        shutil.rmtree(folder, ignore_errors=True)
                        gone += 1
                except OSError:
                    continue
        return gone

    def sweep(self) -> int:
        """Delete the recaps' audio older than the retention. Returns how many recaps' folders
        went. Cached ambience stays (it is reused); the campaign's deletion removes it."""
        cutoff = self._now() - KEEP_DAYS * 86400
        gone = 0
        for folder in self._root.glob("*/*/recaps/*"):
            try:
                if folder.is_dir() and folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    gone += 1
            except OSError:
                continue
        return gone

    # --- signed links ---

    def sign(
        self, secret: bytes, guild_id: int, campaign_id: str, session_at: int, kind: str
    ) -> tuple[int, str]:
        """(expiry, signature) for a short-lived link to one file."""
        self.path(guild_id, campaign_id, session_at, kind)  # the same checks as the path
        expires = int(self._now()) + LINK_SECONDS
        return expires, _mac(secret, guild_id, campaign_id, session_at, kind, expires)

    def verify(
        self,
        secret: bytes,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        kind: str,
        expires: int,
        signature: str,
    ) -> Path | None:
        """The file's path if the link is genuine and not expired; None otherwise."""
        try:
            path = self.path(guild_id, campaign_id, session_at, kind)
        except ValueError:
            return None
        if expires < self._now():
            return None
        good = _mac(secret, guild_id, campaign_id, session_at, kind, expires)
        return path if hmac.compare_digest(good, signature) and path.is_file() else None


def _mac(
    secret: bytes, guild_id: int, campaign_id: str, session_at: int, kind: str, expires: int
) -> str:
    message = f"recap-audio/{guild_id}/{campaign_id}/{session_at}/{kind}/{expires}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()
