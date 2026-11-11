"""0439_private_scene_doors

The website's "Private scenes" list (#4102, the web half of #2996 rule 5): a read door and a "Not
private" door for the campaign's DMs.
"""

from __future__ import annotations

# `memory_private_scenes` is isolated and the web role has no grant on it, so the page reads it
# through a door, as the Names pages do. The list door sends the two characters' names (a merged
# character under the name it became, followed up to six steps as `private_scene_store.roots`
# does), the session's start, about how many minutes and how many lines: ids and times only, no
# word of what was said (the table keeps none), and never `speaker_ids`. A row citing anyone not
# recorded now is left out, with the same rule as `consent.still_recorded` and the met list's door
# (0438); the caller passes the current wording's number. "Not private" is the store's own one-row
# update (`PrivateScenes.not_private`): the bot does nothing extra when a scene is marked, so the
# door makes the change itself, for a DM of the campaign only, and answers `gone` when the scene
# is already marked or not there.
SQL = r"""
    -- The name of the entry that stands for this one now (it, or where its merges led), or NULL.
    CREATE FUNCTION dmbot_entry_name(g BIGINT, c TEXT, eid TEXT) RETURNS TEXT
    LANGUAGE sql STABLE AS $fn$
        WITH RECURSIVE chain(id, name, status, merged_into, hop) AS (
            SELECT e.id, e.name, e.status, e.merged_into, 0 FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = eid
            UNION ALL
            SELECT e.id, e.name, e.status, e.merged_into, ch.hop + 1
            FROM chain ch JOIN memory_entities e
                 ON e.guild_id = g AND e.campaign_id = c AND e.id = ch.merged_into
            WHERE ch.status = 'merged' AND ch.hop < 5
        )
        SELECT h.name FROM chain h WHERE h.status <> 'merged' ORDER BY h.hop LIMIT 1
    $fn$;

    CREATE FUNCTION dmbot_web_private_scenes(g BIGINT, c TEXT, terms INTEGER) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', t.id,
                       'first', COALESCE(dmbot_entry_name(g, c, t.first_id), ''),
                       'second', COALESCE(dmbot_entry_name(g, c, t.second_id), ''),
                       'sessionStartedAt', t.session_started_at,
                       'minutes', GREATEST(1, ((t.to_ms - t.from_ms) / 60000)::INTEGER),
                       'lines', t.line_count)
                       ORDER BY t.session_started_at DESC, t.from_ms DESC, t.id)
            FROM (
                SELECT s.* FROM memory_private_scenes s
                WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'kept'
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(s.speaker_ids) u
                      WHERE u > 0 AND NOT (
                          EXISTS (SELECT 1 FROM consent n
                                  WHERE n.guild_id = g AND n.user_id = u
                                    AND n.terms_version = terms)
                          OR (EXISTS (SELECT 1 FROM import_consents i
                                      WHERE i.guild_id = g AND i.campaign_id = c
                                        AND i.user_id = u AND i.removed_at IS NULL)
                              AND NOT EXISTS (SELECT 1 FROM consent_refusals r
                                              WHERE r.guild_id = g AND r.user_id = u))))
                ORDER BY s.session_started_at DESC, s.from_ms DESC, s.id
                LIMIT 250) t), '[]'::jsonb);
    END
    $fn$;

    -- "Not private": 'ok' with the two names (for the page's line), or 'gone' (already marked, or
    -- not there). NULL for anyone who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_private_scene_not_private(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r memory_private_scenes%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        UPDATE memory_private_scenes s
            SET status = 'not_private',
                decided_at = floor(extract(epoch FROM clock_timestamp()))::BIGINT
            WHERE s.guild_id = g AND s.campaign_id = c AND s.id = sid AND s.status = 'kept'
            RETURNING s.* INTO r;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'first', COALESCE(dmbot_entry_name(g, c, r.first_id), ''),
            'second', COALESCE(dmbot_entry_name(g, c, r.second_id), ''));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_entry_name(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_private_scenes(BIGINT, TEXT, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_private_scene_not_private(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_entry_name(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_private_scenes(BIGINT, TEXT, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_private_scene_not_private(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_private_scenes", "dmbot_web_private_scenes(BIGINT, TEXT, INTEGER)"),
    (
        "dmbot_web_private_scene_not_private",
        "dmbot_web_private_scene_not_private(BIGINT, TEXT, TEXT)",
    ),
)
