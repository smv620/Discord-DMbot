"""0603_card_lines_door

The website reads a character's card lines (#5050 slice d).
"""

from __future__ import annotations

# The three lines at the top of a character's card are kept by the background job in
# `memory_card_summaries` (0600): the sentence, and where and when the character was first met and
# last mentioned. This door gives the DM's page those rows, plus the lore a character was first
# read from (the earliest upload's label, heading and time, merged entries' included, as
# `MemoryStore.lore_origin` reads it) and each session's number by its start, so the page says
# "Session 3, 15 Nov" as Discord does. DM-only like `dmbot_web_scenes`: anyone who is not a DM of
# the campaign gets NULL. Only characters the job has reached have a row, so only they are given a
# card; a page draws nothing for the others. Nothing is written; at most 200 ids are read; no
# transcript words are read (the scene is DMbot's own short description, kept by the job).
SQL = r"""
    CREATE FUNCTION dmbot_web_card_lines(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'cards', COALESCE((
                SELECT jsonb_object_agg(x.id, x.card)
                FROM (
                    SELECT e.id,
                           jsonb_build_object(
                               'who', s.who,
                               'madeAt', s.made_at,
                               'scannedAt', s.scanned_at,
                               'first', CASE WHEN s.first_session IS NULL THEN NULL
                                   ELSE jsonb_build_object('at', s.first_session,
                                       'scene', COALESCE(s.first_scene, '')) END,
                               'last', CASE WHEN s.last_session IS NULL THEN NULL
                                   ELSE jsonb_build_object('at', s.last_session,
                                       'scene', COALESCE(s.last_scene, '')) END,
                               'lore', (
                                   WITH RECURSIVE family(id) AS (
                                       SELECT e.id
                                       UNION SELECT m.id FROM memory_entities m
                                           JOIN family f ON m.merged_into = f.id
                                       WHERE m.guild_id = g AND m.campaign_id = c)
                                   SELECT jsonb_build_object('label', n.label,
                                                             'heading', n.heading,
                                                             'at', n.created_at)
                                   FROM memory_name_sources n JOIN family f
                                       ON n.entity_id = f.id
                                   WHERE n.guild_id = g AND n.campaign_id = c
                                   ORDER BY n.created_at, n.upload_key, (n.heading = ''),
                                            n.entity_id
                                   LIMIT 1)
                           ) AS card
                    FROM memory_entities e
                    JOIN memory_card_summaries s
                        ON s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id
                       AND s.entity_id = e.id
                    WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'confirmed'
                      AND e.merged_into IS NULL AND e.id = ANY (ids[1:200])
                ) x),
                '{}'::jsonb),
            'numbers', COALESCE((
                SELECT jsonb_object_agg(t.started_at::text, t.number)
                FROM (SELECT started_at, line_count,
                             row_number() OVER (ORDER BY started_at, id) AS number
                      FROM transcript_sessions
                      WHERE guild_id = g AND campaign_id = c
                        AND (line_count > 0 OR transcripts_deleted_at IS NOT NULL)) t
                WHERE t.line_count > 0),
                '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_card_lines(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_card_lines(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_card_lines", "dmbot_web_card_lines(BIGINT, TEXT, TEXT[])"),
)
