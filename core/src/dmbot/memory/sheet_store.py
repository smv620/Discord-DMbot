"""Player characters' D&D Beyond sheets, stored per campaign (#723; see
dmbot.memory.sheets). Every query is scoped to one server and one campaign: the same
link in two campaigns is two independent rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.db import Conn, Database, row_int
from dmbot.memory import sheets

NOT_A_CHARACTER = "That isn't a player character in this campaign any more."


class SheetRefused(ValueError):
    """A problem the person can fix; the message is safe to show as-is."""


@dataclass(frozen=True, slots=True)
class CharacterSheet:
    entity_id: str
    name: str  # the character's name in the campaign
    played_by: int | None
    url: str | None
    sheet: dict[str, Any] | None  # sheets.clean()'s shape
    fetched_at: int | None

    @property
    def character(self) -> int | None:
        """The D&D Beyond character number, if linked."""
        return None if self.url is None else sheets.character_id(self.url)


class SheetStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def link(self, guild_id: int, campaign_id: str, entity_id: str, character: int) -> None:
        """Link a player character to a D&D Beyond sheet. A different link drops the old
        snapshot (it was another character's)."""
        url = sheets.sheet_url(character)
        async with self._db.guild(guild_id) as conn:
            await self._require_character(conn, guild_id, campaign_id, entity_id)
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET url = EXCLUDED.url,"
                " sheet = CASE WHEN character_sheets.url = EXCLUDED.url"
                "  THEN character_sheets.sheet END,"
                " source = CASE WHEN character_sheets.url = EXCLUDED.url"
                "  THEN character_sheets.source END,"
                " fetched_at = CASE WHEN character_sheets.url = EXCLUDED.url"
                "  THEN character_sheets.fetched_at END",
                (guild_id, campaign_id, entity_id, url),
            )

    async def save(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        snapshot: dict[str, Any],
        now: int,
        *,
        url: str | None = None,
    ) -> bool:
        """Keep a snapshot. One read from a link (`url`) is kept only if the character is
        still linked to that same sheet (it may have been unlinked or relinked while it
        was being read): False then. A typed one (no `url`) replaces any link."""
        cleaned = sheets.clean(snapshot)
        if cleaned is None:
            raise SheetRefused("That isn't a character sheet DMbot can keep.")
        async with self._db.guild(guild_id) as conn:
            if url is not None:
                cur = await conn.execute(
                    "UPDATE character_sheets SET sheet = %s, source = %s, fetched_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s AND url = %s",
                    (Jsonb(cleaned), cleaned["source"], now, guild_id, campaign_id, entity_id, url),
                )
                return cur.rowcount == 1
            await self._require_character(conn, guild_id, campaign_id, entity_id)
            await conn.execute(
                "INSERT INTO character_sheets"
                " (guild_id, campaign_id, entity_id, url, sheet, source, fetched_at)"
                " VALUES (%s, %s, %s, NULL, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET url = NULL,"
                " sheet = EXCLUDED.sheet, source = EXCLUDED.source,"
                " fetched_at = EXCLUDED.fetched_at",
                (guild_id, campaign_id, entity_id, Jsonb(cleaned), cleaned["source"], now),
            )
            return True

    async def unlink(self, guild_id: int, campaign_id: str, entity_id: str) -> bool:
        """Forget the character's sheet: its link and its snapshot. False if it had none."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM character_sheets"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s",
                (guild_id, campaign_id, entity_id),
            )
            return cur.rowcount > 0

    async def sheets(self, guild_id: int, campaign_id: str) -> list[CharacterSheet]:
        """Every sheet in the campaign, with its character's name and player, by name.
        Characters that were merged away or rejected are left out."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.entity_id, e.name, e.played_by, s.url, s.sheet, s.fetched_at"
                " FROM character_sheets s JOIN memory_entities e"
                "  ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                "  AND e.id = s.entity_id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s"
                "  AND e.status NOT IN ('merged', 'rejected')"
                " ORDER BY lower(e.name), s.entity_id",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            CharacterSheet(
                entity_id=r["entity_id"],
                name=r["name"],
                played_by=row_int(r, "played_by"),
                url=r["url"],
                sheet=None if r["sheet"] is None else sheets.clean(r["sheet"]),
                fetched_at=row_int(r, "fetched_at"),
            )
            for r in rows
        ]

    async def _require_character(
        self, conn: Conn, guild_id: int, campaign_id: str, entity_id: str
    ) -> None:
        # Only a player character can be "played by" someone (the memory store checks
        # that when it's set), so a player is enough to know it's one.
        cur = await conn.execute(
            "SELECT played_by, status FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND id = %s FOR SHARE",
            (guild_id, campaign_id, entity_id),
        )
        row = await cur.fetchone()
        if row is None or row["status"] in ("merged", "rejected") or row["played_by"] is None:
            raise SheetRefused(NOT_A_CHARACTER)
