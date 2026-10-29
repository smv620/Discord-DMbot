"""0253_names_smart

The Names page's search by meaning (#2733).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    ALL_NAMES_KINDS,
)

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
ALL_NAMES_LIST = ALL_NAMES_KINDS.replace(
    "(only_id IS NULL OR v.id = only_id)",
    "(only_id IS NULL OR v.id = ANY (string_to_array(only_id, ',')))",
    1,
)

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
NAMES_SMART = (
    r"""
    -- What an entry is searched by (#2733), as parts: the name and the description, the other
    -- names it is called (not the secret ones) and its connections, each as the relationship's
    -- key, the campaign's own label for it (NULL for a core relationship, whose words are in
    -- code: `memory/entity_text.py` puts the text together), the other name, which way it runs
    -- and whether it reads the same both ways. A connection the DM keeps secret, or one to an
    -- entry the players don't know yet, is left out, so nothing a player may not know is in the
    -- text of a name they may. NULL when the entry has no description (nothing is searched by a
    -- bare name). The bot's numbers and the Names page's reranker both read it, so they read the
    -- same words. The rules for what a name's text may hold are the players' own (the same as
    -- `meaning.entry_texts` and the website's readers): a connection the DM has not revealed, one
    -- to a story-planning entry, or one to an entry whose own name is a secret never goes in. Text
    -- already made can lag until the next embedding run after an entry becomes hidden.
    CREATE FUNCTION dmbot_entity_parts(g BIGINT, c TEXT, eid TEXT) RETURNS JSONB
    LANGUAGE sql STABLE AS $fn$
        SELECT jsonb_build_object(
            'name', e.name,
            'description', e.description,
            'aliases', COALESCE((
                SELECT jsonb_agg(t.text ORDER BY t.text)
                FROM (SELECT DISTINCT a.text FROM memory_aliases a
                      WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                        AND a.status = 'confirmed' AND NOT a.secret
                        AND lower(a.text) <> lower(e.name)
                      ORDER BY a.text LIMIT 6) t), '[]'::jsonb),
            'links', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'predicate', k.predicate, 'label', k.label, 'symmetric', k.symmetric,
                           'other', k.other, 'outgoing', k.outgoing)
                       ORDER BY k.other, k.predicate)
                FROM (SELECT DISTINCT r.predicate, p.label, p.is_symmetric AS symmetric,
                             o.name AS other, (r.subject_id = e.id) AS outgoing
                      FROM memory_relations r
                      LEFT JOIN memory_predicates p
                        ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                       AND p.key = r.predicate
                      JOIN memory_entities o
                        ON o.guild_id = r.guild_id AND o.campaign_id = r.campaign_id
                       AND o.id = CASE WHEN r.subject_id = e.id THEN r.object_id
                                       ELSE r.subject_id END
                      WHERE r.guild_id = g AND r.campaign_id = c
                        AND r.status = 'confirmed' AND NOT r.secret AND NOT r.unrevealed
                        AND (r.subject_id = e.id OR r.object_id = e.id)
                        AND o.status = 'confirmed' AND o.merged_into IS NULL
                        AND o.type <> 'person' AND NOT o.audience_limited
                        AND NOT o.hidden_from_players
                        AND o.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                           'promise', 'deed')
                        AND NOT EXISTS (
                            SELECT 1 FROM memory_aliases sa
                            WHERE sa.guild_id = o.guild_id AND sa.campaign_id = o.campaign_id
                              AND sa.entity_id = o.id AND sa.secret AND sa.status <> 'rejected'
                              AND regexp_replace(lower(sa.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(o.name), '[^[:alnum:]]', '', 'g'))
                      ORDER BY other, predicate LIMIT 40) k), '[]'::jsonb))
        FROM memory_entities e
        WHERE e.guild_id = g AND e.campaign_id = c AND e.id = eid
          AND char_length(btrim(e.description)) > 0
    $fn$;
    REVOKE ALL ON FUNCTION dmbot_entity_parts(BIGINT, TEXT, TEXT) FROM PUBLIC;

    -- The Names page's search by meaning (#2733): the entries whose kept numbers are closest to the
    -- search's numbers (`q`, made by the website's server from the DM's own words), best first,
    -- with the parts of the text each was made from (`dmbot_entity_parts`) so the reranker can read
    -- it. The comparing is done here because the website's role has no right on the numbers. A DM
    -- of the campaign only (NULL for anyone else, a server manager who is not a DM included: the
    -- text holds descriptions, which a manager does not see on the page). One campaign, one
    -- model, the same number of numbers; the vectors are unit length, but the length is divided
    -- out anyway. At most `pool` (60) rows, from the newest 10,000 numbers (about 3 seconds of one
    -- core for 20,000 rows, measured); `capped` says there were more. An empty `q` only proves who
    -- is asking.
    CREATE FUNCTION dmbot_web_names_near(g BIGINT, c TEXT, m TEXT, q REAL[], pool INT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qn DOUBLE PRECISION;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        qn := sqrt((SELECT sum(x::DOUBLE PRECISION * x) FROM unnest(q) AS x));
        IF qn IS NULL OR qn = 0 OR cardinality(q) > 4096 THEN
            RETURN jsonb_build_object('capped', false, 'rows', '[]'::jsonb);
        END IF;
        RETURN jsonb_build_object('capped', EXISTS (
            SELECT 1 FROM memory_vectors x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.owner_kind = 'assertion'
              AND x.model = m AND cardinality(x.vector) = cardinality(q)
            OFFSET 9999), 'rows', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('id', n.id, 'parts', n.parts, 'score', n.score)
                             ORDER BY n.score DESC, n.id)
            FROM (
                SELECT best.id, dmbot_entity_parts(g, c, best.id) AS parts, best.score
                FROM (
                    SELECT split_part(v.owner_id, '#', 1) AS id,
                           max(d.dot / (qn * d.len)) AS score
                    FROM (SELECT x.owner_id, x.vector FROM memory_vectors x
                          WHERE x.guild_id = g AND x.campaign_id = c
                            AND x.owner_kind = 'assertion' AND x.model = m
                            AND cardinality(x.vector) = cardinality(q)
                          ORDER BY x.made_at DESC, x.owner_id LIMIT 10000) v
                    CROSS JOIN LATERAL (
                        SELECT sum(a::DOUBLE PRECISION * b) AS dot,
                               sqrt(sum(a::DOUBLE PRECISION * a)) AS len
                        FROM unnest(v.vector, q) AS u(a, b)) d
                    WHERE d.len > 0
                    GROUP BY 1
                    ORDER BY 2 DESC, 1
                    LIMIT LEAST(GREATEST(pool, 1), 60)
                ) best
                JOIN memory_entities e
                  ON e.guild_id = g AND e.campaign_id = c AND e.id = best.id
                 AND e.status = 'confirmed' AND e.merged_into IS NULL AND e.type <> 'person'
            ) n
            WHERE n.parts IS NOT NULL), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT) FROM PUBLIC;
    """
    + ALL_NAMES_LIST
)

SQL = NAMES_SMART

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The Names page's search by meaning (#2733): the names nearest in meaning, DMs only.
    ("dmbot_web_names_near", "dmbot_web_names_near(BIGINT, TEXT, TEXT, REAL[], INT)"),
)
