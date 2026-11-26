"""0631_web_who_candidates

The website's top guesses for a waiting name, filled by the same ranking as Discord's card (#5264).
"""

from __future__ import annotations

# Two DM-only read doors, like `dmbot_web_who_answers` (0371): anyone but a DM of the campaign gets
# NULL. Nothing is written, and nothing here reads a transcript.
#
# `dmbot_web_who_candidates(g, c, ids)` gives what the ranking needs (`memory/who_ranking.py`, run
# in Python so Discord and the page can never rank differently): for each waiting name in `ids` (at
# most 200, still a suggestion or an unnamed person) its name and kind, and the entries it could be.
# A candidate is a confirmed entry that is not merged, not unnamed and not a table person, with its
# main name and its other confirmed names. As the page's own answer door takes only such an entry
# (0371), an entry with a secret name is left out, so a ready answer is never one that fails;
# Discord alone offers a hidden entry. At most 5,000 entries are read, names only: no
# connection, no line.
#
# `dmbot_web_who_details(g, c, ids)` gives, for at most 20 entries, what the one-sentence detail on
# a choice is made from (`memory/who_top.detail_of`): the card's own sentence if it has one
# (`memory_card_summaries`, filled only while the card summaries are on) and its current ties (a
# tie that is confirmed, not secret and not ended, to a live entry with no secret name). The words
# of the detail are chosen in Python by the function Discord uses.
SQL = r"""
    CREATE FUNCTION dmbot_web_who_candidates(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
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
            'asking', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name, 'kind', e.type)
                                 ORDER BY e.id)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids[1:200])
                  AND e.merged_into IS NULL
                  AND (e.status = 'proposed' OR (e.status = 'confirmed' AND e.unnamed))
            ), '[]'::jsonb),
            'candidates', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', x.id, 'kind', x.type, 'name', x.name,
                           'names', COALESCE((
                               SELECT jsonb_agg(a.text ORDER BY a.id)
                               FROM memory_aliases a
                               WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = x.id
                                 AND a.status = 'confirmed' AND NOT a.secret
                                 AND a.text <> x.name), '[]'::jsonb))
                       ORDER BY x.id)
                FROM (
                    SELECT e.id, e.type, e.name
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'confirmed'
                      AND e.merged_into IS NULL AND NOT e.unnamed AND e.type <> 'person'
                      AND NOT (e.id = ANY (ids[1:200]))
                      AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                      WHERE s.guild_id = g AND s.campaign_id = c
                                        AND s.entity_id = e.id AND s.secret)
                    ORDER BY e.id
                    LIMIT 5000) x
            ), '[]'::jsonb));
    END
    $fn$;

    CREATE FUNCTION dmbot_web_who_details(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(e.id, jsonb_build_object(
                       'summary', (SELECT s.who FROM memory_card_summaries s
                                   WHERE s.guild_id = g AND s.campaign_id = c
                                     AND s.entity_id = e.id),
                       'ties', COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                                      'predicate', r.predicate, 'detail', r.detail,
                                      'outgoing', r.subject_id = e.id, 'other', n.name)
                                      ORDER BY r.created_at, r.id)
                           FROM memory_relations r
                           JOIN memory_entities n
                             ON n.guild_id = r.guild_id AND n.campaign_id = r.campaign_id
                            AND n.id = CASE WHEN r.subject_id = e.id THEN r.object_id
                                            ELSE r.subject_id END
                            AND n.status IN ('proposed', 'confirmed') AND n.merged_into IS NULL
                            AND NOT EXISTS (SELECT 1 FROM memory_aliases s2
                                            WHERE s2.guild_id = g AND s2.campaign_id = c
                                              AND s2.entity_id = n.id AND s2.secret)
                           WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'
                             AND NOT r.secret AND r.to_session_at IS NULL
                             AND e.id IN (r.subject_id, r.object_id)), '[]'::jsonb)))
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (ids[1:20])
              AND e.status = 'confirmed' AND e.merged_into IS NULL AND e.type <> 'person'
        ), '{}'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_who_candidates(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_who_details(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_who_candidates(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_who_details(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_who_candidates", "dmbot_web_who_candidates(BIGINT, TEXT, TEXT[])"),
    ("dmbot_web_who_details", "dmbot_web_who_details(BIGINT, TEXT, TEXT[])"),
)
