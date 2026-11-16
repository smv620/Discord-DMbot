"""0516_game_clock_door

The website can word a time limit's countdown (#4503, quests slice 4a, web half): a DM-only read
door for the campaign's game clock.
"""

from __future__ import annotations

# `dmbot_web_game_clock(g, c)`: NULL for anyone who is not a DM of the campaign (or a server the
# caller is not in), as 0481's door; else the campaign's clock as `{minute, kind, year, ordinal}`,
# or `{}` when it has none. `minute` is the one clock's, or the group furthest ahead while the
# party is split (the clock a time limit is counted on, `time_limits.clock_now`); `kind`, `year`
# and `ordinal` are the calendar setting (`ClockStore.calendar`: Day N, or Harptos with the date of
# Day 1). The website's API turns them into the card's words and never sends the minute on. Read
# only: no table or row changes.
SQL = r"""
    CREATE FUNCTION dmbot_web_game_clock(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        clock game_clocks%ROWTYPE;
        ahead BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO clock FROM game_clocks WHERE guild_id = g AND campaign_id = c;
        IF NOT FOUND THEN
            RETURN '{}'::jsonb;
        END IF;
        SELECT max(minute) INTO ahead FROM game_clock_groups
            WHERE guild_id = g AND campaign_id = c;
        RETURN jsonb_build_object(
            'minute', GREATEST(clock.minute, COALESCE(ahead, 0)),
            'kind', clock.calendar_kind, 'year', clock.calendar_year,
            'ordinal', clock.calendar_ordinal);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_game_clock(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_game_clock(BIGINT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_game_clock", "dmbot_web_game_clock(BIGINT, TEXT)"),
)
