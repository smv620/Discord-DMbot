"""0189_recap_held_names

The website's recap check, as the bot's (#2404).
"""

from __future__ import annotations

# The website's recap check agrees with the bot's (#2404, Supervisor 2026-10-22): a name the players
# haven't been shown yet counts as secret for a recap unless the table said it in that session. The
# check's own door (`dmbot_recap_check_names`) keeps listing such names as secret; this new door,
# for a DM of the campaign, says which of them there are and hands back only the session's table
# lines that could contain one (a loose prefilter; the API reads them exactly, as the bot does).
# Used for this one text and never returned. A new function: no row changes.
RECAP_HELD_NAMES = r"""
    CREATE FUNCTION dmbot_recap_held_names(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        held JSONB;
        hard JSONB;
        said JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- names of entries the players haven't been shown (not a clue, not a person), and the
        -- texts that stay secret whatever was said (a secret spelling, a clue's names)
        held := COALESCE((
            SELECT jsonb_agg(DISTINCT a.text)
            FROM memory_aliases a JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND e.hidden_from_players
              AND NOT e.audience_limited AND NOT a.secret AND a.status = 'confirmed'
              AND e.status = 'confirmed' AND e.type NOT IN ('person', 'clue')), '[]'::jsonb);
        hard := COALESCE((
            SELECT jsonb_agg(DISTINCT a.text)
            FROM memory_aliases a JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND (a.secret OR e.audience_limited)),
            '[]'::jsonb);
        said := COALESCE((
            SELECT jsonb_agg(l.heard || ' ' || COALESCE(l.text, ''))
            FROM (SELECT l2.heard, l2.text FROM transcript_lines l2
                  JOIN transcript_sessions s ON s.id = l2.session_id AND s.guild_id = l2.guild_id
                  WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
                    AND l2.sidebar IS NULL
                    AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(held) h(t)
                        WHERE position(
                                regexp_replace(lower(h.t), '[''’ʼ`-]', '', 'g') IN
                                regexp_replace(lower(l2.heard || ' ' || COALESCE(l2.text, '')),
                                               '[''’ʼ`-]', '', 'g')) > 0)
                  ORDER BY l2.started_ms, l2.id LIMIT 2000) l), '[]'::jsonb);
        RETURN jsonb_build_object('held', held, 'hard', hard, 'said', said);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_held_names(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_held_names(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

SQL = RECAP_HELD_NAMES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_held_names", "dmbot_recap_held_names(BIGINT, TEXT, BIGINT)"),
)
