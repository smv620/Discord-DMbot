"""0421_private_scene_entries

The entries said in each private scene, so their Recurring floor is worked out on read and goes
with the scene (#4110; Supervisor's ruling on #2996, 2026-11-10).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = (
    r"""
    -- Which entries were spoken of inside a private scene, by one of its two (#2996 rule 5): ids
    -- only. Written with the scene by `PrivateScenes.write_session` (the names found in the scene's
    -- own lines, in memory; no word is kept). It goes with its scene (a re-run, the transcript, a
    -- speaker who stops being recorded) and with its entry. The importance floor (at least
    -- Recurring) reads it on every rank, for KEPT scenes only, so the DM's "Not private" and a
    -- consent withdrawal undo it with nothing to clean up: no assessment is stored for it.
    CREATE TABLE memory_private_scene_entries (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        scene_id     TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, scene_id)
            REFERENCES memory_private_scenes (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id    TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        made_by_commit TEXT CONSTRAINT memory_private_scene_entries_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, scene_id, entity_id)
    );
    CREATE INDEX memory_private_scene_entries_entity
        ON memory_private_scene_entries (guild_id, campaign_id, entity_id);
    CREATE TRIGGER memory_private_scene_entries_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_private_scene_entries
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """
    + _isolate("memory_private_scene_entries")
    + r"""
    -- The rank-inputs door (0350) hands the website the same floor input (`private`), so the
    -- website's ranks and the bot's cannot disagree.

    CREATE OR REPLACE FUNCTION dmbot_web_rank_inputs(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'limited', (SELECT count(*) > 5000 FROM memory_entities x
                        WHERE x.guild_id = g AND x.campaign_id = c AND x.type <> 'person'
                          AND x.status IN ('proposed', 'confirmed', 'merged'))
                    OR (SELECT count(*) > 20000 FROM memory_relations x
                        WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'),
            'entities', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'type', e.type, 'role', e.role,
                           'status', e.status, 'mergedInto', e.merged_into) ORDER BY e.id)
                FROM (SELECT * FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.type <> 'person'
                        AND x.status IN ('proposed', 'confirmed', 'merged')
                      ORDER BY x.id LIMIT 5000) e), '[]'::jsonb),
            'relations', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(r.subject_id, r.object_id, r.predicate)
                                 ORDER BY r.id)
                FROM (SELECT * FROM memory_relations x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'
                        AND x.to_session_at IS NULL AND x.predicate <> 'plays'
                        AND x.source IS DISTINCT FROM 'party'
                      ORDER BY x.id LIMIT 20000) r), '[]'::jsonb),
            'assessed', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(a.entity_id, a.seq, a.rank) ORDER BY a.entity_id)
                FROM (SELECT DISTINCT ON (m.entity_id) m.entity_id, m.seq, m.rank
                      FROM memory_importance m
                      WHERE m.guild_id = g AND m.campaign_id = c
                      ORDER BY m.entity_id, m.seq DESC
                      LIMIT 5000) a), '[]'::jsonb),
            'heard', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(h.entity_id, h.session_started_at)
                                 ORDER BY h.entity_id, h.session_started_at)
                FROM (SELECT DISTINCT x.entity_id, x.session_started_at FROM memory_heard x
                      WHERE x.guild_id = g AND x.campaign_id = c
                      ORDER BY x.entity_id, x.session_started_at LIMIT 50000) h), '[]'::jsonb),
            -- (#4110: the entries said in a kept private scene, for the Recurring floor)
            'private', COALESCE((
                SELECT jsonb_agg(p.entity_id ORDER BY p.entity_id)
                FROM (SELECT DISTINCT x.entity_id FROM memory_private_scene_entries x
                      JOIN memory_private_scenes s ON s.guild_id = x.guild_id
                       AND s.campaign_id = x.campaign_id AND s.id = x.scene_id
                      WHERE x.guild_id = g AND x.campaign_id = c AND s.status = 'kept'
                      ORDER BY x.entity_id LIMIT 5000) p), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rank_inputs(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rank_inputs(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("memory_private_scene_entries",)
