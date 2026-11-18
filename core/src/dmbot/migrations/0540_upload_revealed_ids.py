"""0540_upload_revealed_ids

The website lists, for a DM, what a 🌍 Common knowledge upload made known to everyone by itself
(#4661, the web half of #4607; Supervisor's ruling on the issue).
"""

from __future__ import annotations

# Discord builds that list in memory and never logs it; the website needs something stored, so the
# upload's notice record keeps **ids only**, never words, in `revealed_ids`:
# `{"entries": [...], "links": [...], "aliases": [...]}`, at most 50 of each (the page shows ten).
# It goes with the notice record, which goes with its campaign (the foreign key cascades) and
# 30 days after the upload with the notice's own clean-up. It is not in a backup (the notice is
# not).
#
# `dmbot_web_upload_revealed(g, c, kind, sid)`: NULL unless the caller is a DM of the campaign
# (0404's check); `{status: 'none'}` with no such notice; else `{status: 'ok', entries: [{name,
# kind}], links: [{subject, subjectKind, predicate, detail, object}], aliases: [{owner, text}]}`.
# The ids are resolved to words **at read time**, and only what players can see NOW is listed:
# an entry still confirmed and not held back, unnamed or audience-limited; a connection still
# confirmed, not secret and not waiting to be said, with both its ends shown; an other name still
# confirmed and not secret on an entry that is shown. So anything the DM hid again since is never
# listed (Supervisor's ruling). Words and kinds only, never an id. No row changes.
SQL = r"""
    ALTER TABLE upload_notices
        ADD COLUMN revealed_ids JSONB NOT NULL DEFAULT '{}'::jsonb
            CONSTRAINT upload_notices_revealed_ids_check
            CHECK (jsonb_typeof(revealed_ids) = 'object' AND pg_column_size(revealed_ids) <= 12000);

    CREATE FUNCTION dmbot_web_upload_revealed(g BIGINT, c TEXT, kind TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        n upload_notices%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT u.* INTO n FROM upload_notices u
            WHERE u.guild_id = g AND u.campaign_id = c
              AND u.source_kind = kind AND u.source_id = sid
            ORDER BY u.created_at DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'none');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('name', e.name, 'kind', e.type) ORDER BY t.n)
                FROM jsonb_array_elements_text(COALESCE(n.revealed_ids -> 'entries', '[]')
                         ) WITH ORDINALITY AS t(id, n)
                JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c AND e.id = t.id
                WHERE e.status = 'confirmed'
                  AND NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)), '[]'),
            'links', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'subject', s.name, 'subjectKind', s.type, 'predicate', r.predicate,
                           'detail', COALESCE(r.detail, ''), 'object', o.name) ORDER BY t.n)
                FROM jsonb_array_elements_text(COALESCE(n.revealed_ids -> 'links', '[]')
                         ) WITH ORDINALITY AS t(id, n)
                JOIN memory_relations r ON r.guild_id = g AND r.campaign_id = c AND r.id = t.id
                JOIN memory_entities s ON s.guild_id = g AND s.campaign_id = c
                                      AND s.id = r.subject_id
                JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c
                                      AND o.id = r.object_id
                WHERE r.status = 'confirmed' AND NOT (r.secret OR r.unrevealed)
                  AND s.status = 'confirmed'
                  AND NOT (s.hidden_from_players OR s.unnamed OR s.audience_limited)
                  AND o.status = 'confirmed'
                  AND NOT (o.hidden_from_players OR o.unnamed OR o.audience_limited)), '[]'),
            'aliases', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('owner', e.name, 'text', a.text)
                                 ORDER BY t.n)
                FROM jsonb_array_elements_text(COALESCE(n.revealed_ids -> 'aliases', '[]')
                         ) WITH ORDINALITY AS t(id, n)
                JOIN memory_aliases a ON a.guild_id = g AND a.campaign_id = c AND a.id = t.id
                JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c
                                      AND e.id = a.entity_id
                WHERE a.status = 'confirmed' AND NOT a.secret
                  AND e.status = 'confirmed'
                  AND NOT (e.hidden_from_players OR e.unnamed OR e.audience_limited)), '[]'));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_upload_revealed(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_upload_revealed(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_upload_revealed", "dmbot_web_upload_revealed(BIGINT, TEXT, TEXT, TEXT)"),
)
