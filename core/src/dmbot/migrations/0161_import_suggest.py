"""0161_import_suggest

Who a name is, and who they play (#2135).
"""

from __future__ import annotations

IMPORT_SUGGEST = r"""
    -- The import page's suggestions (#2135): for each name in a file, who it probably is and which
    -- character they play. Read-only, never decides anything: the DM confirms. `wanted` is a
    -- list of {label, user, shown, member}: `user` is the username part of Craig's
    -- "Display (username)", `shown` the display part, `member` a person already known for the
    -- label (remembered from an earlier import). A person is looked for by username, then by
    -- display name or server nickname, then by the names the campaign's table already uses for
    -- them; the first step that finds anyone decides, and two people at that step suggest no one.
    -- Members come from the server's directory (only while their consent is current) and the
    -- campaign's own people. Characters: the one they already play, else a confirmed character
    -- called what the label is called. Never another server's or campaign's names. NULL unless a
    -- DM of the campaign.
    CREATE FUNCTION dmbot_import_suggest(g BIGINT, c TEXT, wanted JSONB, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out JSONB := '{}'::jsonb;
        w JSONB;
        u TEXT;
        s TEXT;
        lbl TEXT;
        uid BIGINT;
        who TEXT;
        why TEXT;
        found BIGINT[];
        agreed BOOLEAN;
        plays TEXT;
        plays_why TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF wanted IS NULL OR jsonb_typeof(wanted) <> 'array' THEN
            RETURN out;
        END IF;
        FOR w IN SELECT e FROM jsonb_array_elements(wanted) WITH ORDINALITY AS t(e, n)
                 WHERE t.n <= 100 LOOP
            lbl := coalesce(w->>'label', '');
            u := lower(btrim(left(coalesce(w->>'user', ''), 100)));
            s := lower(btrim(left(coalesce(w->>'shown', ''), 100)));
            uid := NULL; who := NULL; why := NULL; plays := NULL; plays_why := NULL;
            found := NULL;
            IF lbl = '' THEN
                CONTINUE;
            END IF;
            IF coalesce(w->>'member', '') ~ '^[0-9]{1,19}$'
               AND (length(w->>'member') < 19 OR w->>'member' <= '9223372036854775807')
               AND (w->>'member')::BIGINT > 0
               AND (EXISTS (SELECT 1 FROM member_directory d
                            WHERE d.guild_id = g AND d.user_id = (w->>'member')::BIGINT)
                    OR EXISTS (SELECT 1 FROM memory_entities p
                               WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                                 AND p.played_by = (w->>'member')::BIGINT)
                    OR EXISTS (SELECT 1 FROM consent x
                               WHERE x.guild_id = g AND x.user_id = (w->>'member')::BIGINT))
            THEN
                uid := (w->>'member')::BIGINT;  -- remembered from an earlier import
                why := 'remembered';
                who := '';
            ELSE
                -- 1. the username part
                IF u <> '' THEN
                    SELECT array_agg(d.user_id) INTO found FROM member_directory d
                        WHERE d.guild_id = g AND lower(d.username) = u
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = d.user_id
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF found IS NOT NULL THEN
                        why := 'username';
                    END IF;
                END IF;
                -- 2. the display name or server nickname
                IF found IS NULL AND (s <> '' OR u <> '') THEN
                    SELECT array_agg(d.user_id) INTO found FROM member_directory d
                        WHERE d.guild_id = g AND d.display_name <> ''
                          AND lower(d.display_name) IN (s, u)
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = d.user_id
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF found IS NOT NULL THEN
                        why := 'display_name';
                    END IF;
                END IF;
                -- 3. the names the table already uses for a person
                IF found IS NULL AND s <> '' THEN
                    SELECT array_agg(DISTINCT p.played_by) INTO found FROM memory_entities p
                        WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                          AND p.status = 'confirmed' AND p.played_by IS NOT NULL
                          AND NOT p.audience_limited
                          AND EXISTS (SELECT 1 FROM memory_aliases a
                                      WHERE a.guild_id = g AND a.campaign_id = c
                                        AND a.entity_id = p.id AND a.status = 'confirmed'
                                        AND NOT a.secret AND lower(a.text) = s
                                        AND (lower(p.name) <> s OR a.key = (
                                             SELECT n.key FROM memory_aliases n
                                             WHERE n.guild_id = g AND n.campaign_id = c
                                               AND n.entity_id = p.id AND n.text = p.name
                                               AND n.status <> 'rejected'
                                             ORDER BY n.id LIMIT 1)));
                    IF found IS NOT NULL THEN
                        why := 'people';
                    END IF;
                END IF;
                IF found IS NOT NULL AND cardinality(found) = 1 THEN
                    uid := found[1];
                    found := NULL;
                ELSE
                    why := NULL;  -- nobody, or more than one at the deciding step: the DM picks
                END IF;
                IF uid IS NOT NULL THEN
                    -- The server's own name for them only while they are recorded now; else the
                    -- campaign's name for the person.
                    SELECT coalesce(nullif(d.display_name, ''), d.username) INTO who
                        FROM member_directory d
                        WHERE d.guild_id = g AND d.user_id = uid
                          AND EXISTS (SELECT 1 FROM consent x
                                      WHERE x.guild_id = g AND x.user_id = uid
                                        AND x.terms_version = terms
                                        AND (outside IS NULL OR x.outside_to = outside));
                    IF who IS NULL THEN
                        SELECT p.name INTO who FROM memory_entities p
                            WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                              AND p.status = 'confirmed' AND p.played_by = uid
                              AND NOT p.audience_limited
                              AND EXISTS (SELECT 1 FROM memory_aliases a
                                          WHERE a.guild_id = g AND a.campaign_id = c
                                            AND a.entity_id = p.id AND a.text = p.name
                                            AND a.status = 'confirmed' AND NOT a.secret
                                            AND a.key = (SELECT n.key FROM memory_aliases n
                                                         WHERE n.guild_id = g AND n.campaign_id = c
                                                           AND n.entity_id = p.id
                                                           AND n.text = p.name
                                                           AND n.status <> 'rejected'
                                                         ORDER BY n.id LIMIT 1))
                            ORDER BY p.created_at, p.id LIMIT 1;
                    END IF;
                    who := coalesce(who, '');
                END IF;
            END IF;
            -- The character: the one they already play, else one called what the label is.
            IF uid IS NOT NULL THEN
                SELECT ch.name INTO plays
                    FROM memory_relations r
                    JOIN memory_entities p ON p.guild_id = r.guild_id
                         AND p.campaign_id = r.campaign_id AND p.id = r.subject_id
                    JOIN memory_entities ch ON ch.guild_id = r.guild_id
                         AND ch.campaign_id = r.campaign_id AND ch.id = r.object_id
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'plays'
                      AND r.status = 'confirmed' AND r.to_session_at IS NULL AND NOT r.secret
                      AND p.type = 'person' AND p.played_by = uid AND p.status = 'confirmed'
                      AND ch.status = 'confirmed' AND ch.type = 'character'
                      AND NOT ch.audience_limited
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = ch.id AND a.text = ch.name
                                    AND a.status = 'confirmed' AND NOT a.secret
                                    AND a.key = (SELECT n.key FROM memory_aliases n
                                                 WHERE n.guild_id = g AND n.campaign_id = c
                                                   AND n.entity_id = ch.id AND n.text = ch.name
                                                   AND n.status <> 'rejected'
                                                 ORDER BY n.id LIMIT 1))
                    ORDER BY ch.created_at DESC, ch.id LIMIT 1;
                IF plays IS NOT NULL THEN
                    plays_why := 'plays';
                END IF;
            END IF;
            IF plays IS NULL AND s <> '' THEN
                SELECT ch.name INTO plays FROM memory_entities ch
                    WHERE ch.guild_id = g AND ch.campaign_id = c AND ch.type = 'character'
                      AND ch.status = 'confirmed' AND ch.played_by IS NULL
                      AND lower(ch.name) = s
                      AND NOT ch.audience_limited
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = ch.id AND a.text = ch.name
                                    AND a.status = 'confirmed' AND NOT a.secret
                                    AND a.key = (SELECT n.key FROM memory_aliases n
                                                 WHERE n.guild_id = g AND n.campaign_id = c
                                                   AND n.entity_id = ch.id AND n.text = ch.name
                                                   AND n.status <> 'rejected'
                                                 ORDER BY n.id LIMIT 1))
                    ORDER BY ch.created_at, ch.id LIMIT 1;
                IF plays IS NOT NULL THEN
                    plays_why := 'same_name';
                END IF;
            END IF;
            agreed := uid IS NOT NULL AND EXISTS (
                SELECT 1 FROM consent x
                WHERE x.guild_id = g AND x.user_id = uid AND x.terms_version = terms
                  AND (outside IS NULL OR x.outside_to = outside));
            out := out || jsonb_build_object(lbl, jsonb_build_object(
                'member', CASE WHEN uid IS NULL THEN NULL ELSE jsonb_build_object(
                    'userId', uid::TEXT, 'name', who, 'why', why, 'agreed', agreed) END,
                'character', CASE WHEN plays IS NULL THEN NULL ELSE jsonb_build_object(
                    'name', plays, 'why', plays_why) END));
        END LOOP;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
"""

SQL = IMPORT_SUGGEST

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_import_suggest", "dmbot_import_suggest(BIGINT, TEXT, JSONB, INT, TEXT)"),
)
