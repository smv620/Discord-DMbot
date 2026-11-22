"""0592_web_first_feeling

The website shows the first meeting's feeling reminder under a waiting "knows" claim (#4942, the
web half of #4229 slice 4b), level with Discord's "🤔 Mithral Keep is unfriendly toward dwarves…".
"""

from __future__ import annotations

# `dmbot_web_first_feeling_info(g, c, ids)` gives the facts the reminder is worked out from, the way
# `dmbot_web_which_info` (0342) gives a "Which one?" its facts: the page asks only for the ids of
# the waiting claims its own list showed, and the line is worked out in Python by the store's own
# pure functions (`claims.feeling_pairs`, `claims.feeling_lines`, `first_feeling.line`), so the
# website cannot drift from Discord's card. Worked out each time and never stored. DM only
# (anyone else gets NULL). For the proposed claims named in `ids`: their words and the entries the
# DM picked for a name; the campaign's live entries and other names (secret ones flagged, as the
# store reads them); every confirmed connection touching an entry (ended ones too: two once
# connected have met); the DM's open, confirmed feelings (`status = 'confirmed'`, never one DMbot
# only read) and the entries' lineage links. Ids, the DM's own words, names and numbers, never a
# transcript line, a quote or a speaker (not even a claim's line numbers). At most 200 ids, 5000
# entries, 10000 names, 50000 connections (the newest first, since a missing one could show a
# reminder for two who have met), 5000 feelings and 5000 lineage links: a campaign beyond that gets
# fewer lines, never an error. No row changes.
SQL = r"""
    CREATE FUNCTION dmbot_web_first_feeling_info(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF ids IS NULL OR cardinality(ids) = 0 OR cardinality(ids) > 200 THEN
            RETURN jsonb_build_object(
                'claims', '[]'::jsonb, 'entities', '[]'::jsonb, 'aliases', '[]'::jsonb,
                'relations', '[]'::jsonb, 'homes', '[]'::jsonb, 'species', '[]'::jsonb);
        END IF;
        RETURN jsonb_build_object(
            'claims', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', k.id, 'subject', k.subject, 'relationship', k.relationship,
                    'object', k.object, 'details', k.details,
                    'subject_picked', k.subject_picked, 'object_picked', k.object_picked)
                    ORDER BY k.created_at, k.id)
                FROM memory_claims k
                WHERE k.guild_id = g AND k.campaign_id = c AND k.id = ANY (ids)
                  AND k.status = 'proposed'), '[]'::jsonb),
            'entities', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', e.id, 'type', e.type, 'name', e.name, 'status', e.status,
                    'source', e.source, 'role', e.role, 'played_by', e.played_by) ORDER BY e.id)
                FROM (SELECT * FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c
                        AND x.status IN ('proposed', 'confirmed') AND x.type <> 'person'
                      ORDER BY x.id LIMIT 5000) e), '[]'::jsonb),
            'aliases', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'entity_id', a.entity_id, 'text', a.text, 'key', a.key, 'kind', a.kind,
                    'source', a.source, 'secret', a.secret) ORDER BY a.id)
                FROM (SELECT * FROM memory_aliases y
                      WHERE y.guild_id = g AND y.campaign_id = c AND y.status <> 'rejected'
                      ORDER BY y.id LIMIT 10000) a), '[]'::jsonb),
            'relations', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'subject_id', r.subject_id, 'object_id', r.object_id,
                    'predicate', r.predicate, 'detail', r.detail,
                    'to_session_at', r.to_session_at) ORDER BY r.id)
                FROM (SELECT * FROM memory_relations z
                      WHERE z.guild_id = g AND z.campaign_id = c AND z.status = 'confirmed'
                      ORDER BY z.created_at DESC, z.id LIMIT 50000) r), '[]'::jsonb),
            'homes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'holder_id', h.holder_id, 'toward_id', h.toward_id,
                    'toward_lineage', h.toward_lineage, 'score', h.score) ORDER BY h.id)
                FROM (SELECT * FROM memory_attitudes w
                      WHERE w.guild_id = g AND w.campaign_id = c AND w.status = 'confirmed'
                        AND w.closed_at IS NULL
                      ORDER BY w.id LIMIT 5000) h), '[]'::jsonb),
            'species', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('entity_id', l.entity_id, 'name', l.name)
                                 ORDER BY l.created_at, l.id)
                FROM (SELECT * FROM memory_rule_links v
                      WHERE v.guild_id = g AND v.campaign_id = c AND v.kind = 'species'
                      ORDER BY v.created_at, v.id LIMIT 5000) l), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_first_feeling_info(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_first_feeling_info(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_first_feeling_info", "dmbot_web_first_feeling_info(BIGINT, TEXT, TEXT[])"),
)
