"""0616_web_name_found

What made each waiting name a name, for the website's name card (#5144, the website half of #5133
slice 3c): a read-only door beside `dmbot_recap_names_read`.
"""

from __future__ import annotations

# `dmbot_recap_name_found(g, c, at_)` answers, for the campaign's DMs only (NULL for anyone else),
# one object for each of the recap's waiting names that has a `name_found` row (0608, 0611): how it
# was found, how many times it was heard and how many of those were typed, its type, role and
# source (the card's guess is worked out from them by `ui/names.background_kind`'s own rule, in
# `web/name_found.py`), its other spellings (not secret, not rejected) and the one line that made
# it a name. The line is read now: its words and speaker's name are sent only when that speaker has
# a consent row for this server at this moment and the session's transcript is not deleted; a line
# whose speaker no longer agrees comes back with no words at all, and the page leaves it out with
# nothing in its place (as Discord's card does). Nothing is copied or kept, and nothing is
# written. A name with a secret spelling is left out, as in the recap's other doors.
SQL = r"""
    CREATE FUNCTION dmbot_recap_name_found(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
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
            SELECT jsonb_object_agg(r.id, r.found)
            FROM (
                SELECT e.id, jsonb_build_object(
                    'how', f.how, 'times', f.times, 'typed', f.typed,
                    'type', e.type, 'role', e.role, 'source', e.source,
                    'createdAt', e.created_at,
                    'also', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('text', a.text, 'key', a.key)
                                         ORDER BY a.key, a.id)
                        FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                          AND NOT a.secret AND a.status <> 'rejected'), '[]'::jsonb),
                    'line', (
                        SELECT jsonb_build_object(
                                   'ms', l.started_ms, 'startedAt', t.started_at,
                                   'agreed', ok.agreed,
                                   -- (words and name only for a speaker who agrees: the rest
                                   -- never leave the database)
                                   'text', CASE WHEN ok.agreed
                                           THEN COALESCE(l.text, l.heard) ELSE '' END,
                                   'name', CASE WHEN ok.agreed
                                           THEN COALESCE(t.speaker_names ->> l.user_id::TEXT, '')
                                           ELSE '' END)
                        FROM transcript_sessions t
                        JOIN transcript_lines l
                          ON l.guild_id = t.guild_id AND l.session_id = t.id
                        CROSS JOIN LATERAL (
                            SELECT EXISTS (SELECT 1 FROM consent n
                                           WHERE n.guild_id = g AND n.user_id = l.user_id)
                                   AS agreed) ok
                        WHERE f.line_ref ~ '^.*:[0-9]{1,18}:[0-9]{1,18}$'
                          AND t.guild_id = g AND t.campaign_id = c
                          AND t.id = substring(f.line_ref from '^(.*):[0-9]{1,18}:[0-9]{1,18}$')
                          AND t.transcripts_deleted_at IS NULL
                          AND l.started_ms = substring(
                                  f.line_ref from ':([0-9]{1,18}):[0-9]{1,18}$')::BIGINT
                          AND l.user_id = substring(f.line_ref from ':([0-9]{1,18})$')::BIGINT
                          AND l.sidebar IS NULL
                          AND (l.topic = 'game' OR NOT EXISTS (
                               SELECT 1 FROM session_imports i
                               WHERE i.guild_id = g AND i.session_id = t.id))
                        ORDER BY l.id LIMIT 1))
                    AS found
                FROM recap_name_checks k
                JOIN memory_entities e
                  ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                 AND e.id = k.entity_id
                JOIN name_found f
                  ON f.guild_id = e.guild_id AND f.campaign_id = e.campaign_id
                 AND f.entity_id = e.id
                WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
                  AND e.status = 'proposed'
                  AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                  WHERE s.guild_id = g AND s.campaign_id = c
                                    AND s.entity_id = e.id AND s.secret)) r), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_name_found(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_name_found(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_name_found", "dmbot_recap_name_found(BIGINT, TEXT, BIGINT)"),
)
