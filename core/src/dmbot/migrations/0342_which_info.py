"""0342_which_info

What the website needs to show a kept claim's "Which one?" choices (#3463).
"""

from __future__ import annotations

# (The reason text is `claims.UNPLACED_REASON`, copied here because a migration cannot import the
# store; a test keeps the two equal.)
UNPLACED_REASON = "kept for your answer: a name in it could be two different entries"

# A claim kept because a name in it could be two entries (#3238, #3423) is answered with "Which
# one?". The choices are worked out in Python by the store's own function (`claims.which_choices`,
# so the website cannot drift from Discord's card) from the facts this door sends. For the claims
# named in `ids` that are such kept claims (the page asks only for ones its own list showed, so
# the listed claims' recorded-speaker rule has already been applied) and for the campaign: the
# live entries, their non-rejected other names (the secret ones flagged: the store never uses
# them) and the confirmed connections that give an entry its tag ("in Termalaine"). DM only
# (anyone else gets NULL); ids, the claim's own words, names, kinds, notes and the DM's
# descriptions, never a transcript line, a quote or a speaker. At most 200 ids, 5000 entries,
# 10000 names and 5000 connections: a campaign beyond that gets a shorter list, never an error.
WHICH_INFO = (
    r"""
    CREATE FUNCTION dmbot_web_which_info(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF ids IS NULL OR cardinality(ids) = 0 OR cardinality(ids) > 200 THEN
            RETURN jsonb_build_object('claims', '[]'::jsonb, 'entities', '[]'::jsonb,
                                      'aliases', '[]'::jsonb, 'ties', '[]'::jsonb);
        END IF;
        RETURN jsonb_build_object(
            'claims', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', k.id, 'subject', k.subject, 'object', k.object,
                    'subject_picked', k.subject_picked, 'object_picked', k.object_picked)
                    ORDER BY k.created_at, k.id)
                FROM memory_claims k
                WHERE k.guild_id = g AND k.campaign_id = c AND k.id = ANY (ids)
                  AND k.status = 'proposed' AND k.reason = '"""
    + UNPLACED_REASON
    + r"""'), '[]'::jsonb),
            'entities', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', e.id, 'type', e.type, 'name', e.name, 'description', e.description,
                    'status', e.status, 'source', e.source, 'unnamed', e.unnamed,
                    'unnamed_anchor', e.unnamed_anchor, 'unnamed_role', e.unnamed_role,
                    'unnamed_what', e.unnamed_what) ORDER BY e.id)
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
            'ties', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'subject_id', r.subject_id, 'object_id', r.object_id,
                    'predicate', r.predicate, 'secret', r.secret, 'detail', r.detail)
                    ORDER BY r.id)
                FROM (SELECT * FROM memory_relations z
                      WHERE z.guild_id = g AND z.campaign_id = c AND z.status = 'confirmed'
                      ORDER BY z.id LIMIT 5000) r), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_which_info(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_which_info(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
)

SQL = WHICH_INFO

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_which_info", "dmbot_web_which_info(BIGINT, TEXT, TEXT[])"),
)
