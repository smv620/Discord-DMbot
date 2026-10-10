"""A campaign's linked house-rules file (#969): the share link a DM set once, and the
fingerprint of the file they chose to "ignore until it changes".

Per campaign, never shared between campaigns or servers; deleted with the campaign; not in
backups (a share link can be private). Only the campaign's DMs set, clear or ignore: each
checks that in the same transaction. The link is never logged and never shown back in full:
`FileLink.site` is the only part that is.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import yarl

from dmbot import fetch
from dmbot.db import Database
from dmbot.rules.house import HouseRuleError, require_dm

LINK_MAX = 2000
NO_LINK = "There's no house-rules file linked yet."
BAD_LINK = "That doesn't look like a link. Paste the share link of your house-rules file."
TOO_LONG = f"That link is too long (over {LINK_MAX} characters)."


@dataclass(frozen=True, slots=True)
class FileLink:
    link: str  # private: never logged, never shown
    ignored: str | None  # the fingerprint of the file the DM chose to ignore until it changes
    set_by: int
    set_at: int

    @property
    def site(self) -> str:
        """Where the file is, and nothing else: `docs.google.com`."""
        try:
            host = yarl.URL(fetch.direct_url(self.link).human_repr()).host
        except fetch.LinkError:
            host = None
        return host or "a link"


def clean_link(raw: str) -> str:
    """The link as typed, tidied. Raises HouseRuleError in plain words if it isn't one."""
    text = raw.strip().strip("<>")
    if not text:
        raise HouseRuleError(BAD_LINK)
    if len(text) > LINK_MAX:
        raise HouseRuleError(TOO_LONG)
    try:
        fetch.direct_url(text)
    except fetch.LinkError as exc:
        raise HouseRuleError(str(exc)) from None
    return text


class HouseFileLinkStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def get(self, guild_id: int, campaign_id: str) -> FileLink | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT link, ignored, set_by, set_at FROM house_rules_file"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return FileLink(row["link"], row["ignored"], int(row["set_by"]), int(row["set_at"]))

    async def set_link(self, guild_id: int, campaign_id: str, user_id: int, link: str) -> FileLink:
        """Link (or re-link) the file. A new link forgets what was ignored."""
        text = clean_link(link)
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            await conn.execute(
                "INSERT INTO house_rules_file (guild_id, campaign_id, link, ignored, set_by,"
                " set_at) VALUES (%s, %s, %s, NULL, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET link = EXCLUDED.link,"
                " ignored = NULL, set_by = EXCLUDED.set_by, set_at = EXCLUDED.set_at",
                (guild_id, campaign_id, text, user_id, now),
            )
        return FileLink(text, None, user_id, now)

    async def clear(self, guild_id: int, campaign_id: str, user_id: int) -> bool:
        """Unlink the file. True if there was one."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM house_rules_file WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return cur.rowcount > 0

    async def ignore(self, guild_id: int, campaign_id: str, user_id: int, fingerprint: str) -> bool:
        """Say nothing about this version of the file again (until it changes). False if no
        file is linked any more."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "UPDATE house_rules_file SET ignored = %s WHERE guild_id = %s AND campaign_id = %s",
                (fingerprint, guild_id, campaign_id),
            )
            return cur.rowcount > 0
