"""0634_web_adventure_list

What the Shared story adventure holds, for the website (#3694, slice A).
"""

from __future__ import annotations

# The DM-only read door for the page that shows what DMbot holds of the DM's adventure, as
# Discord's Shared story card does: each file given, how far its read is, and what the read found.
# **Counts and file names only.** Not a word of the adventure leaves the database through it
# (`adventure_chunks` and `plan_nodes` are not read for their text: only counted), and the file
# names are the DM's own words about their file, which only a DM of the campaign is given. Anyone
# else, a member who is not a DM included, gets NULL, as does anyone who is not in the server.
# Nothing is written.
SQL = r"""
    CREATE FUNCTION dmbot_web_adventure_list(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'mode', (SELECT k.story_mode FROM campaigns k WHERE k.guild_id = g AND k.id = c),
            'documents', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', d.id, 'name', d.file_name, 'characters', d.characters,
                           'restored', d.chunks = 0, 'addedAt', d.added_at,
                           'read', (SELECT jsonb_build_object(
                                        'status', r.status, 'nextChunk', r.next_chunk,
                                        'totalChunks', r.total_chunks,
                                        'stopReason', r.stop_reason,
                                        'skippedParts', r.skipped_parts,
                                        'updatedAt', r.updated_at)
                                    FROM adventure_reads r
                                    WHERE r.guild_id = g AND r.campaign_id = c
                                      AND r.document_id = d.id),
                           'found', jsonb_build_object(
                               'chapters', (SELECT count(*) FROM plan_nodes n
                                            WHERE n.guild_id = g AND n.campaign_id = c
                                              AND n.document_id = d.id AND n.kind = 'chapter'),
                               'places', (SELECT count(*) FROM plan_nodes n
                                          WHERE n.guild_id = g AND n.campaign_id = c
                                            AND n.document_id = d.id AND n.kind = 'place'),
                               'people', (SELECT count(*) FROM plan_nodes n
                                          WHERE n.guild_id = g AND n.campaign_id = c
                                            AND n.document_id = d.id AND n.kind = 'person'),
                               'links', (SELECT count(*) FROM plan_links l
                                         WHERE l.guild_id = g AND l.campaign_id = c
                                           AND l.document_id = d.id)))
                           ORDER BY d.added_at, d.id)
                FROM adventure_documents d
                WHERE d.guild_id = g AND d.campaign_id = c), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_list(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_list(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_adventure_list", "dmbot_web_adventure_list(BIGINT, TEXT)"),
)
