"""0243_recap_present

Who was in each scene, saved by the DM (#2684).
"""

from __future__ import annotations

# The recap page's "Present: ..." for each scene (#2684, #2577): the DM's read of who was in each
# scene and of the campaign's player characters they may pick from, and the save of one scene's
# list. Names only, never a transcript word; for a DM of the campaign alone (the read gives NULL
# to anyone else, the save says `not_found`). The list is kept on the beat with `saved: true`, so
# nothing works it out again; "Make it again" clears the beats, and so the lists (the page says
# so first). A new function pair: no row changes.
RECAP_PRESENT_DOORS = r"""
    -- (A scene's number `n` is its place in the stored sheet's array, as the page reads it back.
    -- DMbot writes the sheet as objects only; anything else is skipped here and refused below.
    -- Two characters of one campaign with the same name are listed once.)
    CREATE FUNCTION dmbot_recap_present(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE sheet JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT r.beats INTO sheet FROM recap_jobs r
         WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'beats', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'n', t.ord,
                    'present', CASE WHEN jsonb_typeof(t.b -> 'present') = 'array'
                                    THEN t.b -> 'present' ELSE '[]'::jsonb END,
                    'saved', COALESCE((t.b -> 'saved') = 'true'::jsonb, false))
                    ORDER BY t.ord)
                FROM jsonb_array_elements(
                         CASE WHEN jsonb_typeof(sheet) = 'array' THEN sheet ELSE '[]'::jsonb END
                     ) WITH ORDINALITY AS t(b, ord)
                WHERE jsonb_typeof(t.b) = 'object'), '[]'::jsonb),
            'characters', COALESCE((
                SELECT jsonb_agg(DISTINCT e.name) FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.role = 'player_character'
                  AND e.played_by IS NOT NULL AND e.status = 'confirmed'), '[]'::jsonb));
    END
    $fn$;
    -- Save scene `n`'s list (its place in the sheet, as the read numbers it). Gives {"n",
    -- "present" (the campaign's order), "saved": true} or {"error": not_found | not_allowed |
    -- stale | bad_present}. Only a recap that is ready and still a draft; the DM's word wins, so
    -- a list once saved is replaced only by the DM.
    CREATE FUNCTION dmbot_recap_set_present(g BIGINT, c TEXT, at_ BIGINT, n INT, names JSONB)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        chosen TEXT[];
        asked INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN jsonb_build_object('error', 'not_found');
        END IF;
        SELECT * INTO job FROM recap_jobs
         WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('error', 'not_found');
        END IF;
        IF job.state <> 'ready' OR job.status <> 'draft' THEN
            RETURN jsonb_build_object('error', 'not_allowed');
        END IF;
        IF jsonb_typeof(job.beats) IS DISTINCT FROM 'array' OR n < 1
           OR n > jsonb_array_length(job.beats)
           OR jsonb_typeof(job.beats -> (n - 1)) IS DISTINCT FROM 'object' THEN
            RETURN jsonb_build_object('error', 'stale');
        END IF;
        IF jsonb_typeof(names) IS DISTINCT FROM 'array' OR jsonb_array_length(names) > 12
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(names) x
                      WHERE jsonb_typeof(x) IS DISTINCT FROM 'string') THEN
            RETURN jsonb_build_object('error', 'bad_present');
        END IF;
        SELECT count(DISTINCT x) INTO asked FROM jsonb_array_elements_text(names) x;
        SELECT COALESCE(array_agg(DISTINCT e.name), '{}') INTO chosen
          FROM memory_entities e
         WHERE e.guild_id = g AND e.campaign_id = c AND e.role = 'player_character'
           AND e.played_by IS NOT NULL AND e.status = 'confirmed'
           AND e.name IN (SELECT jsonb_array_elements_text(names));
        IF asked <> jsonb_array_length(names) OR cardinality(chosen) <> asked THEN
            RETURN jsonb_build_object('error', 'bad_present');
        END IF;
        UPDATE recap_jobs SET beats = (
            SELECT jsonb_agg(CASE WHEN t.ord = n
                                  THEN t.b || jsonb_build_object('present', to_jsonb(chosen),
                                                                 'saved', true)
                                  ELSE t.b END ORDER BY t.ord)
            FROM jsonb_array_elements(job.beats) WITH ORDINALITY AS t(b, ord))
         WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN jsonb_build_object('n', n, 'present', to_jsonb(chosen), 'saved', true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_present(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_present(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)
        FROM PUBLIC;
"""

SQL = RECAP_PRESENT_DOORS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_present", "dmbot_recap_present(BIGINT, TEXT, BIGINT)"),  # (#2684, #2577)
    (
        "dmbot_recap_set_present",
        "dmbot_recap_set_present(BIGINT, TEXT, BIGINT, INT, JSONB)",
    ),
)
