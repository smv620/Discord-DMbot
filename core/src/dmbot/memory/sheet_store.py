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


@dataclass(frozen=True, slots=True)
class PlayerCharacter:
    """A character someone plays, in one campaign of a server."""

    campaign_id: str
    campaign_name: str
    entity_id: str
    name: str


class SheetStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def link(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        character: int,
        *,
        player: int | None = None,
    ) -> None:
        """Link a player character to a D&D Beyond sheet. A different link drops the old
        snapshot (it was another character's). `player`: only if they play it (the
        player's own panel; the DM's forms leave it out)."""
        url = sheets.sheet_url(character)
        async with self._db.guild(guild_id) as conn:
            plays = await self._require_character(conn, guild_id, campaign_id, entity_id, player)
            # The snapshot stays only for the same link by the same player.
            same = "character_sheets.url = EXCLUDED.url AND character_sheets.player_id = %s"
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, player_id)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET url = EXCLUDED.url,"
                " player_id = EXCLUDED.player_id,"
                f" sheet = CASE WHEN {same} THEN character_sheets.sheet END,"
                f" source = CASE WHEN {same} THEN character_sheets.source END,"
                f" fetched_at = CASE WHEN {same} THEN character_sheets.fetched_at END",
                (guild_id, campaign_id, entity_id, url, plays, plays, plays, plays),
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
        player: int | None = None,
    ) -> bool:
        """Keep a snapshot. One read from a link (`url`) is kept only if the character is
        still linked to that same sheet (it may have been unlinked or relinked while it
        was being read): False then. That UPDATE deliberately doesn't check the entry is
        still a played character: the row was only made for one, and it goes with the
        entry. A typed one (no `url`) replaces any link."""
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
            plays = await self._require_character(conn, guild_id, campaign_id, entity_id, player)
            await conn.execute(
                "INSERT INTO character_sheets"
                " (guild_id, campaign_id, entity_id, url, sheet, source, fetched_at, player_id)"
                " VALUES (%s, %s, %s, NULL, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET url = NULL,"
                " sheet = EXCLUDED.sheet, source = EXCLUDED.source,"
                " fetched_at = EXCLUDED.fetched_at, player_id = EXCLUDED.player_id",
                (guild_id, campaign_id, entity_id, Jsonb(cleaned), cleaned["source"], now, plays),
            )
            return True

    async def unlink(
        self, guild_id: int, campaign_id: str, entity_id: str, *, player: int | None = None
    ) -> bool:
        """Forget the character's sheet: its link and its snapshot. False if it had none
        (or, with `player`, if they don't play it)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM character_sheets s"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.entity_id = %s"
                " AND (%s::bigint IS NULL OR EXISTS (SELECT 1 FROM memory_entities e"
                "  WHERE e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                "  AND e.id = s.entity_id AND e.played_by = %s))",
                (guild_id, campaign_id, entity_id, player, player),
            )
            return cur.rowcount > 0

    async def sheets(
        self, guild_id: int, campaign_id: str, *, entity_id: str | None = None
    ) -> list[CharacterSheet]:
        """Every sheet in the campaign (or one character's), with its character's name and
        player, by name. Only characters someone plays: not ones merged away, rejected,
        or left without a player (an undone merge)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.entity_id, e.name, e.played_by, s.url, s.sheet, s.fetched_at"
                " FROM character_sheets s JOIN memory_entities e"
                "  ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                "  AND e.id = s.entity_id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s"
                "  AND (%s::text IS NULL OR s.entity_id = %s)"
                "  AND e.status NOT IN ('merged', 'rejected') AND e.played_by IS NOT NULL"
                # Only the sheet of whoever plays it now: never a previous player's.
                "  AND s.player_id = e.played_by"
                " ORDER BY lower(e.name), s.entity_id",
                (guild_id, campaign_id, entity_id, entity_id),
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

    async def sheet(
        self, guild_id: int, campaign_id: str, entity_id: str, *, player: int | None = None
    ) -> CharacterSheet | None:
        """One character's sheet, if it has one (with `player`: only if they play it)."""
        found = await self.sheets(guild_id, campaign_id, entity_id=entity_id)
        return next((s for s in found if player is None or s.played_by == player), None)

    async def characters_of(
        self, guild_id: int, user_id: int, campaign_id: str | None = None
    ) -> list[PlayerCharacter]:
        """The characters this person plays in this server (or in one campaign of it),
        by campaign and name: what their "My character sheet" button can link."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT e.campaign_id, c.name AS campaign_name, e.id, e.name"
                " FROM memory_entities e JOIN campaigns c"
                "  ON c.guild_id = e.guild_id AND c.id = e.campaign_id"
                " WHERE e.guild_id = %s AND e.played_by = %s"
                "  AND e.status NOT IN ('merged', 'rejected')"
                "  AND (%s::text IS NULL OR e.campaign_id = %s)"
                " ORDER BY lower(c.name), lower(e.name), e.id",
                (guild_id, user_id, campaign_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            PlayerCharacter(r["campaign_id"], r["campaign_name"], r["id"], r["name"]) for r in rows
        ]

    async def _require_character(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        player: int | None = None,
    ) -> int:
        """Who plays it (refused unless someone does, and with `player`, them). Only a
        player character can be "played by" someone (the memory store checks that when
        it's set), so a player is enough to know it's one."""
        cur = await conn.execute(
            "SELECT played_by, status FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND id = %s FOR SHARE",
            (guild_id, campaign_id, entity_id),
        )
        row = await cur.fetchone()
        if (
            row is None
            or row["status"] in ("merged", "rejected")
            or row["played_by"] is None
            or (player is not None and row["played_by"] != player)
        ):
            raise SheetRefused(NOT_A_CHARACTER)
        return int(row["played_by"])
