"""The campaign's pronunciation dictionary, in its backup (an `ExportSection`; #2552).

One row (`recap_dictionaries`): the id and version of the dictionary made at the voice company and
the hash of the rules it was made from. The recap audio step reuses it while the hash still
matches the names, and makes a new one when it does not, so a restored campaign loses nothing and
a dictionary the company no longer has is replaced the next time the names change. Only
non-secret names are ever in the dictionary itself.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

DAMAGED = "This backup file is damaged (bad pronunciation dictionary)."
_KEYS = frozenset({"dictionary_id", "version_id", "rules_hash", "at"})


class DictionarySection:
    name = "recap_dictionaries"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT dictionary_id, version_id, rules_hash, updated_at FROM recap_dictionaries"
            " WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        return [
            {
                "dictionary_id": str(r["dictionary_id"]),
                "version_id": str(r["version_id"]),
                "rules_hash": str(r["rules_hash"]),
                "at": int(r["updated_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) > 1:
            raise CampaignError(DAMAGED)
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != _KEYS
                or not all(isinstance(row[k], str) for k in ("dictionary_id", "version_id"))
                or not 1 <= len(row["dictionary_id"]) <= 100
                or not 1 <= len(row["version_id"]) <= 100
                or not isinstance(row["rules_hash"], str)
                or len(row["rules_hash"]) != 64
                or not isinstance(row["at"], int)
                or isinstance(row["at"], bool)
                or not 0 <= row["at"] <= 2**63 - 1
            ):
                raise CampaignError(DAMAGED)
        return list(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for row in self.check(rows):
            await conn.execute(
                "INSERT INTO recap_dictionaries (guild_id, campaign_id, dictionary_id, version_id,"
                " rules_hash, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, row["dictionary_id"], row["version_id"],
                 row["rules_hash"], row["at"]),
            )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_dictionaries WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
