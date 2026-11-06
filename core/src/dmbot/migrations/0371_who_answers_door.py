"""0371_who_answers_door

The DM-only door for the AI's answers to "Who is this?" (#3602, slice 3).
"""

from __future__ import annotations

SQL = r"""
    -- What DMbot thinks each waiting name is (`memory_who_answers`, 0370), for the website's one-
    -- name page and the Questions page. Only a DM of the campaign is given anything: anyone else,
    -- a member who is not a DM included, gets NULL, as does anyone who is not a member of the
    -- server. Each answer names the entry it points at by its main name and kind, so the DM can
    -- read it ("a.k.a. Ilse"); a hidden or secret entry is named too, because only a DM can ask.
    -- Only while the question is open (the name is still a suggestion or an unnamed person, not
    -- merged away). An answer that points at an entry is given only when the page's own answer
    -- door would take it ("same as" needs a confirmed entry that is not a table person and has no
    -- secret spelling, as `dmbot_recap_names_read`'s `known` list and `dmbot_name_answer_add`),
    -- so a ready answer is never one that fails; a hidden entry is therefore a choice in Discord
    -- only. `targetTie` is the name of one confirmed, visible entry it is tied to, for the tag of
    -- an entry whose name only describes it (#3431). Ids, kind words, names and a confidence
    -- word: never a line of a transcript. Nothing is written; at most 200 ids are read.
    CREATE FUNCTION dmbot_web_who_answers(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
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
        RETURN jsonb_build_object('status', 'ok', 'answers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', a.entity_id, 'rank', a.rank, 'pick', a.pick,
                       'targetId', a.target_id, 'targetName', t.name, 'targetKind', t.type,
                       'targetTie', (
                           SELECT n.name FROM memory_relations r
                           JOIN memory_entities n
                             ON n.guild_id = r.guild_id AND n.campaign_id = r.campaign_id
                            AND n.id = CASE WHEN r.subject_id = t.id THEN r.object_id
                                            ELSE r.subject_id END
                            AND n.status = 'confirmed' AND n.merged_into IS NULL
                            AND n.type <> 'person'
                            AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                            WHERE s.guild_id = g AND s.campaign_id = c
                                              AND s.entity_id = n.id AND s.secret)
                           WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'
                             AND NOT r.secret AND t.id IN (r.subject_id, r.object_id)
                           ORDER BY r.created_at, r.id LIMIT 1),
                       'newKind', a.new_kind, 'confidence', a.confidence)
                   ORDER BY a.entity_id, a.rank)
            FROM memory_who_answers a
            JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
             AND e.merged_into IS NULL
             AND (e.status = 'proposed' OR (e.status = 'confirmed' AND e.unnamed))
            LEFT JOIN memory_entities t
              ON t.guild_id = a.guild_id AND t.campaign_id = a.campaign_id AND t.id = a.target_id
             AND t.merged_into IS NULL AND t.status = 'confirmed' AND t.type <> 'person'
             AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                             WHERE s.guild_id = g AND s.campaign_id = c
                               AND s.entity_id = t.id AND s.secret)
            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ANY (ids[1:200])
              AND (a.pick = 'new' OR t.id IS NOT NULL)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_who_answers(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_who_answers(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_who_answers", "dmbot_web_who_answers(BIGINT, TEXT, TEXT[])"),
)
