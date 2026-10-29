"""0153_import_recaps

Write recaps for an import, its scan window (#2106).
"""

from __future__ import annotations

IMPORT_RECAPS = """
    -- Recaps of imported sessions (#2106): the DM's per-session "Write recaps" choice, and where
    -- the bot has got with the session's name scan. `scan_from`/`scan_to` are the seconds in
    -- which that scan proposed its names, so the recap can wait for the questions from THIS
    -- session only (a live session counts by its own start time; an old one has none).
    -- Defaulted columns change no rows, so no row-level-security policy is needed.
    ALTER TABLE session_imports
        ADD COLUMN write_recap BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN scanned     BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN scan_from   BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN scan_to     BIGINT NOT NULL DEFAULT 0,
        -- failed tries of the scan (it is given up on after a few), and whether the recap for
        -- this session has been dealt with (made, held, or decided against).
        ADD COLUMN scan_tries  INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN recap_started BOOLEAN NOT NULL DEFAULT FALSE,
        -- `scanned` is the claim (scan_from is when it was taken); a claim that never finished
        -- (the bot stopped) is taken again after a while.
        ADD COLUMN scan_finished BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN recap_tries INTEGER NOT NULL DEFAULT 0;
    -- The holds of one import act as a group (one question, one reminder, one expiry line, one
    -- "Write the recap now"): they share the import's id. Empty for a live session's hold.
    ALTER TABLE recap_holds ADD COLUMN group_id TEXT NOT NULL DEFAULT '';

    -- dmbot_import_write_recap: set the choice of one stored import, only while it waits to be
    -- cleaned and only by a DM of the campaign in a server on the session's list. True when set.
    CREATE FUNCTION dmbot_import_write_recap(g BIGINT, c TEXT, sid TEXT, on_ BOOLEAN)
    RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN FALSE;
        END IF;
        UPDATE session_imports SET write_recap = on_
            WHERE guild_id = g AND campaign_id = c AND session_id = sid AND state = 'waiting';
        RETURN FOUND;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SQL = IMPORT_RECAPS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The "Write recaps" choice of one imported session (#2106).
    ("dmbot_import_write_recap", "dmbot_import_write_recap(BIGINT, TEXT, TEXT, BOOLEAN)"),
)
