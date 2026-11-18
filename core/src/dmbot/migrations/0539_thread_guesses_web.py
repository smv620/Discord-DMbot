"""0539_thread_guesses_web

The website shows the DM a story thread's guesses (#4665, the web half of quests slice 7b): one
read door, for a DM of the campaign and nobody else.
"""

from __future__ import annotations

# `dmbot_web_thread_guesses(g, c, t)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0533's door; `{status: 'not_found'}` for an entry that is not a kept
# one of this campaign; else `{status: 'ok', guesses: [{id, holder, subject, predicate, detail,
# object, confidence, accusation, seq}]}`, newest first, the rows `MemoryStore.thread_guesses`
# gives the DM (`Viewer.the_dm()`):
# - only a guess still current: its belief is the newest of its holder's on its connection (the
#   highest `seq` for that connection and holder), more than 0.0 sure, and the connection was not
#   turned down (`status <> 'rejected'`);
# - the holder is the party (`holder` is '') or a player character or NPC with a name; the truth's
#   and everyone's perspectives are no guess of the table's and are left out;
# - the words are built in Python from the connection's own words, as the store builds them, so
#   Discord's and the website's cannot drift.
# The DM sees every guess, so no connection or name is hidden from this door's caller. Nothing is
# worked out here: no right or wrong, no "solved" (§7). Words and numbers only, no id of a belief
# is sent (`id` is the guess's own link). No row changes.
SQL = r"""
    CREATE FUNCTION dmbot_web_thread_guesses(g BIGINT, c TEXT, t TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM memory_entities x
                       WHERE x.guild_id = g AND x.campaign_id = c AND x.id = t
                         AND x.status = 'confirmed') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'guesses', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', l.id,
                       'holder', CASE WHEN v.kind = 'party' THEN '' ELSE h.name END,
                       'subject', s.name, 'predicate', r.predicate,
                       'detail', COALESCE(r.detail, ''), 'object', o.name,
                       'confidence', b.confidence, 'accusation', b.status = 'accusation',
                       'seq', b.seq) ORDER BY b.seq DESC, l.id)
            FROM memory_thread_guesses l
            JOIN memory_beliefs b ON b.guild_id = l.guild_id AND b.campaign_id = l.campaign_id
                                 AND b.id = l.belief_id
            JOIN memory_relations r ON r.guild_id = b.guild_id AND r.campaign_id = b.campaign_id
                                   AND r.id = b.relation_id AND r.status <> 'rejected'
            JOIN memory_perspectives v ON v.guild_id = b.guild_id
                                      AND v.campaign_id = b.campaign_id
                                      AND v.id = b.perspective_id
            LEFT JOIN memory_entities h ON h.guild_id = v.guild_id AND h.campaign_id = v.campaign_id
                                       AND h.id = v.entity_id
            JOIN memory_entities s ON s.guild_id = r.guild_id AND s.campaign_id = r.campaign_id
                                  AND s.id = r.subject_id
            JOIN memory_entities o ON o.guild_id = r.guild_id AND o.campaign_id = r.campaign_id
                                  AND o.id = r.object_id
            WHERE l.guild_id = g AND l.campaign_id = c AND l.thread_id = t
              AND b.confidence > 0.0
              AND (v.kind = 'party' OR (v.kind IN ('pc', 'npc') AND h.id IS NOT NULL))
              AND b.seq = (SELECT max(n.seq) FROM memory_beliefs n
                           WHERE n.guild_id = b.guild_id AND n.campaign_id = b.campaign_id
                             AND n.relation_id = b.relation_id
                             AND n.perspective_id = b.perspective_id)
        ), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_thread_guesses(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_thread_guesses(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_thread_guesses", "dmbot_web_thread_guesses(BIGINT, TEXT, TEXT)"),
)
