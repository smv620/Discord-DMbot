"""0151_recap_hold_names

How many answers it waits on (#2174).
"""

from __future__ import annotations

RECAP_HOLD_NAMES = """
    -- How many name questions a held recap waits on (#2174), written by the bot each time it counts
    -- (at the hold and in every round), so the website can say "N answers to give" without
    -- reading memory. A defaulted column changes no rows, so no row-level-security policy is
    -- needed. The website reads it only through the door below.
    ALTER TABLE recap_holds ADD COLUMN names_open INTEGER NOT NULL DEFAULT 0
        CHECK (names_open >= 0);

    -- The signed-in person's held recaps in one server: only campaigns they are a DM of, only
    -- holds the DM was told about and that still wait on an answer (a silent hold is waiting for
    -- the story extractor, and one with nothing left is released by the next round). Counts and
    -- times only; no name or text. The server must be one of the session's, as for the other
    -- website doors (a second layer: forced row-level security already holds the same line).
    CREATE FUNCTION dmbot_web_recap_holds(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN '[]'::jsonb;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'campaignId', h.campaign_id, 'campaign', c.name,
                       'sessionAt', h.session_at, 'namesOpen', h.names_open)
                   ORDER BY h.session_at DESC)
            FROM recap_holds h
            JOIN campaigns c ON c.guild_id = h.guild_id AND c.id = h.campaign_id
            WHERE h.guild_id = g AND h.asked AND h.names_open > 0
              AND EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = h.guild_id AND d.campaign_id = h.campaign_id
                            AND d.user_id = dmbot_current_user())), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_holds(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_recap_holds(BIGINT) FROM PUBLIC;
"""

SQL = RECAP_HOLD_NAMES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The signed-in DM's recaps still waiting for their name answers (#2174).
    ("dmbot_web_recap_holds", "dmbot_web_recap_holds(BIGINT)"),
)
