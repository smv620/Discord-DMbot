"""0029_sheet_player."""

from __future__ import annotations

SHEET_PLAYER = """
    -- Who linked or typed in a character's sheet (#723 review): the player playing it
    -- then. A sheet is shown and used only while that same person plays the character,
    -- so a character given to someone else never shows the last player's link or
    -- details (whichever way it changed hands, an undo included). Rows from before this
    -- belong to whoever plays the character now. Migrations run with no server set, so
    -- row-level security hides every row: the backfill opens the table for itself.
    ALTER TABLE character_sheets ADD COLUMN player_id BIGINT;
    -- Open every table the statements read, not only the one they write (CLAUDE.md):
    -- the UPDATE reads memory_entities too.
    CREATE POLICY migrate_backfill ON character_sheets FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    UPDATE character_sheets s SET player_id = e.played_by
        FROM memory_entities e
        WHERE e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id AND e.id = s.entity_id;
    DELETE FROM character_sheets WHERE player_id IS NULL;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON character_sheets;
    ALTER TABLE character_sheets ALTER COLUMN player_id SET NOT NULL;
    """

SQL = SHEET_PLAYER
