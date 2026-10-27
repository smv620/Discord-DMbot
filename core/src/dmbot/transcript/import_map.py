"""Who a label in an import file was, remembered per campaign (#2105), and in its backup.

A file names its speakers by label ("Cerric (stormy#1234)"). The DM ties each label to a server
member once; the website remembers it (`dmbot_import_mapping`) so the next file's "Cerric" is
suggested as the same member. Only the label and the account are kept, never a word said. Deleted
with the campaign, and in its backup like every campaign table (CLAUDE.md: campaign isolation).
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

MAX_LABELS = 500
LABEL_MAX = 80
INT64_MAX = 2**63 - 1
DAMAGED = "This backup file is damaged (bad import names)."


class ImportSpeakersSection:
    """A campaign's remembered import labels in its backups (an `ExportSection`)."""

    name = "import_speakers"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT label, user_id, updated_at FROM import_speakers"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY label",
            (guild_id, campaign_id),
        )
        return [
            {"label": r["label"], "user": str(r["user_id"]), "at": int(r["updated_at"])}
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[tuple[str, int, int]]:
        if not isinstance(rows, list) or len(rows) > MAX_LABELS:
            raise CampaignError(DAMAGED)
        out: list[tuple[str, int, int]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"label", "user", "at"}:
                raise CampaignError(DAMAGED)
            label, user, at = row["label"], row["user"], row["at"]
            digits = isinstance(user, str) and user.isascii() and user.isdecimal()
            if (
                not isinstance(label, str)
                or not 1 <= len(label) <= LABEL_MAX
                or label in seen
                or not digits
                or not 0 < int(str(user)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            seen.add(label)
            out.append((label, int(str(user)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = rows if self._checked(rows) else self.check(rows)
        for label, user, at in checked:
            await conn.execute(
                "INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (guild_id, campaign_id, label, user, at),
            )

    @staticmethod
    def _checked(rows: Any) -> bool:
        return (
            isinstance(rows, list)
            and bool(rows)
            and all(isinstance(r, tuple) and len(r) == 3 for r in rows)
        )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM import_speakers WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
