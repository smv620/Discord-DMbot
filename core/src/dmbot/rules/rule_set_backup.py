"""The sets a campaign publishes, in its backup (an `ExportSection`; #2552).

A campaign's own shared sets (`rule_sets` with their versions and co-editors) are its content, so
a backup carries them: a campaign must never be lost if its DM disappears. NEVER the share codes
(`rule_set_codes`) or the open suggestions: a code is the key to the set and must not travel in a
file anyone in the server may download, so a restored publisher makes a new code. A set's id is
unique across the whole database, so a restored set gets a new id (restoring next to the original
cannot clash); a campaign that followed the old id keeps its own copy of the rules, as always.
"""

from __future__ import annotations

import secrets
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.rules import pack_content
from dmbot.rules.rule_sets import HOUSE_RULES, NAME_MAX, RuleSetError, _json, valid_content

SETS_MAX = 1 + pack_content.PACKS_MAX * 4  # one house-rules set and a handful of packs
VERSIONS_MAX = 200  # versions of one set
EDITORS_MAX = 20
DAMAGED = "This backup file is damaged (bad shared sets)."
_SET_KEYS = frozenset({"kind", "name", "status", "by", "at", "versions", "editors"})
_VERSION_KEYS = frozenset({"version", "content", "by", "at", "source"})
_EDITOR_KEYS = frozenset({"user", "by", "at"})
_SOURCES = ("publisher", "suggestion", "document")
_INT64 = 2**63 - 1


def _user(raw: object) -> bool:
    return isinstance(raw, str) and raw.isascii() and raw.isdigit() and 0 < int(raw) <= _INT64


def _num(raw: object, low: int, high: int = _INT64) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool) and low <= raw <= high


class RuleSetsSection:
    name = "rule_sets"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, kind, name, status, created_by, created_at FROM rule_sets"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY created_at, id LIMIT %s",
            (guild_id, campaign_id, SETS_MAX),
        )
        out: list[Any] = []
        for s in await cur.fetchall():
            cur = await conn.execute(
                "SELECT version, content, created_by, created_at, source FROM rule_set_versions"
                " WHERE guild_id = %s AND campaign_id = %s AND set_id = %s ORDER BY version"
                " LIMIT %s",
                (guild_id, campaign_id, s["id"], VERSIONS_MAX),
            )
            versions = [
                {
                    "version": int(v["version"]),
                    "content": _json(v["content"]),
                    "by": str(v["created_by"]),
                    "at": int(v["created_at"]),
                    "source": str(v["source"]),
                }
                for v in await cur.fetchall()
            ]
            cur = await conn.execute(
                "SELECT user_id, added_by, added_at FROM rule_set_editors WHERE guild_id = %s"
                " AND campaign_id = %s AND set_id = %s ORDER BY added_at, user_id LIMIT %s",
                (guild_id, campaign_id, s["id"], EDITORS_MAX),
            )
            editors = [
                {"user": str(e["user_id"]), "by": str(e["added_by"]), "at": int(e["added_at"])}
                for e in await cur.fetchall()
            ]
            out.append(
                {
                    "kind": str(s["kind"]),
                    "name": str(s["name"]),
                    "status": str(s["status"]),
                    "by": str(s["created_by"]),
                    "at": int(s["created_at"]),
                    "versions": versions,
                    "editors": editors,
                }
            )
        return out

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Every row validated before anything is written."""
        if not isinstance(rows, list) or len(rows) > SETS_MAX:
            raise CampaignError(DAMAGED)
        out: list[dict[str, Any]] = []
        houses = 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != _SET_KEYS:
                raise CampaignError(DAMAGED)
            kind, name = row["kind"], row["name"]
            if (
                kind not in (HOUSE_RULES, "homebrew")
                or not isinstance(name, str)
                or not 1 <= len(name) <= NAME_MAX
                or row["status"] not in ("shared", "unshared")
                or not _user(row["by"])
                or not _num(row["at"], 0)
                or not isinstance(row["versions"], list)
                or not 1 <= len(row["versions"]) <= VERSIONS_MAX
                or not isinstance(row["editors"], list)
                or len(row["editors"]) > EDITORS_MAX
            ):
                raise CampaignError(DAMAGED)
            houses += kind == HOUSE_RULES
            numbers: set[int] = set()
            versions = []
            for v in row["versions"]:
                if (
                    not isinstance(v, dict)
                    or set(v) != _VERSION_KEYS
                    or not _num(v["version"], 1, 2**31 - 1)
                    or v["version"] in numbers
                    or not _user(v["by"])
                    or not _num(v["at"], 0)
                    or v["source"] not in _SOURCES
                ):
                    raise CampaignError(DAMAGED)
                numbers.add(v["version"])
                try:
                    content = valid_content(v["content"], kind)
                except RuleSetError:
                    raise CampaignError(DAMAGED) from None
                versions.append(v | {"content": content})
            people: set[str] = set()
            for e in row["editors"]:
                if (
                    not isinstance(e, dict)
                    or set(e) != _EDITOR_KEYS
                    or not _user(e["user"])
                    or e["user"] in people
                    or not _user(e["by"])
                    or not _num(e["at"], 0)
                ):
                    raise CampaignError(DAMAGED)
                people.add(e["user"])
            out.append(row | {"versions": versions})
        if houses > 1:  # a campaign shares one house-rules set
            raise CampaignError(DAMAGED)
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for row in self.check(rows):
            set_id = secrets.token_hex(16)  # new: a set's id is unique across every campaign
            await conn.execute(
                "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, set_id, row["kind"], row["name"], row["status"],
                 int(row["by"]), row["at"]),
            )  # fmt: skip
            for v in row["versions"]:
                await conn.execute(
                    "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version,"
                    " content,"
                    " created_by, created_at, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (guild_id, campaign_id, set_id, v["version"], _jsonb(v["content"]),
                     int(v["by"]), v["at"], v["source"]),
                )  # fmt: skip
            for e in row["editors"]:
                await conn.execute(
                    "INSERT INTO rule_set_editors (guild_id, campaign_id, set_id, user_id,"
                    " added_by, added_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (guild_id, campaign_id, set_id, int(e["user"]), int(e["by"]), e["at"]),
                )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        # (versions and editors go with their set)
        await conn.execute(
            "DELETE FROM rule_sets WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


def _jsonb(content: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(content)
