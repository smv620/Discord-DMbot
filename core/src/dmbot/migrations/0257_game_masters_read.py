"""0257_game_masters_read

A campaign's game masters, and who to offer.
"""

from __future__ import annotations

GAME_MASTERS_READ = r"""
    -- A campaign's game masters for the website's Settings tab (#2768), and the people who can be
    -- offered as a new one. Read only; both are for a DM of the campaign alone (NULL for anyone
    -- else), in a server on the session's list. A name is the campaign's own person for that
    -- account if there is one, else the name the bot last saw in the server, else the website's.
    -- The website's name table is opened for the one call (as the import page's member search
    -- does) and shut again. The candidates are the campaign's own people and the server's members
    -- who agreed to be recorded under the current terms (`terms`, as the import page's search
    -- passes it; an older agreement is not one), never a game master already, at most 25: no
    -- free-typed account ever reaches the queue, and nobody who has not agreed is listed.
    CREATE FUNCTION dmbot_web_game_masters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        listed JSONB;
        owner_id BIGINT;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT k.owner_user_id INTO owner_id
        FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'userId', d.user_id::TEXT,
                   'name', COALESCE(
                       (SELECT e.name FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                          AND e.status = 'confirmed' AND e.played_by = d.user_id
                        ORDER BY e.name LIMIT 1),
                       (SELECT CASE WHEN m.display_name <> '' THEN m.display_name
                                    ELSE m.username END
                        FROM member_directory m
                        WHERE m.guild_id = g AND m.user_id = d.user_id
                          AND (m.display_name <> '' OR m.username <> '')),
                       (SELECT u.display_name FROM web_users u
                        WHERE u.user_id = d.user_id AND COALESCE(u.display_name, '') <> ''),
                       ''),
                   'plan', d.user_id = owner_id,
                   'you', d.user_id = dmbot_current_user())
               ORDER BY (d.user_id = owner_id) DESC, d.user_id), '[]'::jsonb)
        INTO listed
        FROM campaign_dms d WHERE d.guild_id = g AND d.campaign_id = c;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN listed;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_game_masters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_game_masters(BIGINT, TEXT) FROM PUBLIC;

    CREATE FUNCTION dmbot_web_game_master_candidates(g BIGINT, c TEXT, q TEXT, terms INT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        listed JSONB;
        before TEXT := COALESCE(current_setting('dmbot.import_names', true), '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.import_names', 'on', true);
        SELECT COALESCE(jsonb_agg(jsonb_build_object('userId', m.user_id::TEXT, 'name', m.name)
                        ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name)),
                        '[]'::jsonb)
        INTO listed
        FROM (SELECT one.user_id, one.name, one.rank FROM (
                  -- One row for each account: the campaign's name for the person if there is one,
                  -- else the server directory's. A search matches either name.
                  SELECT n.user_id,
                         (array_agg(n.name ORDER BY n.rank, lower(n.name)))[1] AS name,
                         min(n.rank) AS rank,
                         lower(string_agg(n.hay, ' ')) AS hay
                  FROM (
                      SELECT e.played_by AS user_id, e.name, 0 AS rank, e.name AS hay
                      FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'person'
                        AND e.status = 'confirmed' AND e.played_by IS NOT NULL
                      UNION ALL
                      SELECT m.user_id,
                             CASE WHEN m.display_name <> '' THEN m.display_name ELSE m.username END,
                             1, m.display_name || ' ' || m.username
                      FROM member_directory m
                      WHERE m.guild_id = g AND (m.display_name <> '' OR m.username <> '')
                  ) n
                  WHERE EXISTS (SELECT 1 FROM consent x
                                WHERE x.guild_id = g AND x.user_id = n.user_id
                                  AND x.terms_version = terms)
                    AND NOT EXISTS (SELECT 1 FROM campaign_dms d
                                    WHERE d.guild_id = g AND d.campaign_id = c
                                      AND d.user_id = n.user_id)
                  GROUP BY n.user_id
              ) one
              WHERE q = '' OR position(lower(q) IN one.hay) > 0
              ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)
              LIMIT 25) m;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN listed;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
"""

SQL = GAME_MASTERS_READ

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_game_masters", "dmbot_web_game_masters(BIGINT, TEXT)"),
    (
        "dmbot_web_game_master_candidates",
        "dmbot_web_game_master_candidates(BIGINT, TEXT, TEXT, INT)",
    ),
)
