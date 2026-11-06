"""0163_import_plays

The DM's "plays" choices wait for the bot (#2135).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

IMPORT_PLAYS = (
    f"""
    -- Who plays which character, chosen by the DM on the website's import page (#2135 part B).
    -- The website's role cannot write story memory, so the choice waits here, one per member and
    -- campaign, and the bot makes it a DM-confirmed link (the names panel's own path) in its
    -- import round, then deletes the row. A wait, not content: left out of backups.
    CREATE TABLE import_plays (
        {_memory_scope()}
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        character_id TEXT NOT NULL,
        created_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, user_id)
    );
    """
    + _isolate("import_plays")
    + r"""
    -- dmbot_import_plays: stores the DM's choices, [{"user": "<member id>", "name": "<character>"}]
    -- (at most 100). NULL unless the caller is a DM of the campaign. A choice is kept only for a
    -- member recorded now, and for one confirmed character with that name that nobody plays, is
    -- not limited to some players, and whose own name is a confirmed, non-secret one (the rule
    -- the transcript export uses). Returns how many were kept.
    CREATE FUNCTION dmbot_import_plays(g BIGINT, c TEXT, wanted JSONB, terms INT, outside TEXT)
    RETURNS INT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        w JSONB;
        uid BIGINT;
        found TEXT[];
        kept INT := 0;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF wanted IS NULL OR jsonb_typeof(wanted) <> 'array' OR terms < 6 THEN
            RETURN 0;
        END IF;
        FOR w IN SELECT e FROM jsonb_array_elements(wanted) WITH ORDINALITY AS t(e, n)
                 WHERE t.n <= 100 LOOP
            IF jsonb_typeof(w) <> 'object'
               OR coalesce(w->>'user', '') !~ '^[0-9]{1,19}$'
               OR (length(w->>'user') = 19 AND w->>'user' > '9223372036854775807')
               OR (w->>'user')::BIGINT <= 0
               OR btrim(coalesce(w->>'name', '')) = '' THEN
                CONTINUE;
            END IF;
            uid := (w->>'user')::BIGINT;
            IF NOT EXISTS (SELECT 1 FROM consent x
                           WHERE x.guild_id = g AND x.user_id = uid AND x.terms_version = terms
                             AND (outside IS NULL OR x.outside_to = outside)) THEN
                CONTINUE;
            END IF;
            -- A name shared by two live characters (played, limited or not) is the DM's to pick
            -- in Discord: the free one is not guessed.
            IF (SELECT count(*) FROM memory_entities t
                WHERE t.guild_id = g AND t.campaign_id = c AND t.type = 'character'
                  AND t.status IN ('proposed', 'confirmed')
                  AND lower(t.name) = lower(btrim(left(w->>'name', 100)))) <> 1 THEN
                CONTINUE;
            END IF;
            SELECT array_agg(ch.id) INTO found FROM memory_entities ch
                WHERE ch.guild_id = g AND ch.campaign_id = c AND ch.type = 'character'
                  AND ch.status = 'confirmed' AND ch.played_by IS NULL
                  AND lower(ch.name) = lower(btrim(left(w->>'name', 100)))
                  AND NOT ch.audience_limited
                  AND EXISTS (SELECT 1 FROM memory_aliases a
                              WHERE a.guild_id = g AND a.campaign_id = c
                                AND a.entity_id = ch.id AND a.text = ch.name
                                AND a.status = 'confirmed' AND NOT a.secret
                                AND a.key = (SELECT n.key FROM memory_aliases n
                                             WHERE n.guild_id = g AND n.campaign_id = c
                                               AND n.entity_id = ch.id AND n.text = ch.name
                                               AND n.status <> 'rejected'
                                             ORDER BY n.id LIMIT 1));
            IF found IS NULL OR cardinality(found) <> 1 THEN
                CONTINUE;  -- none, or two with one name: the DM picks it in Discord
            END IF;
            INSERT INTO import_plays (guild_id, campaign_id, user_id, character_id, created_at)
                VALUES (g, c, uid, found[1], now_s)
                ON CONFLICT (guild_id, campaign_id, user_id)
                DO UPDATE SET character_id = EXCLUDED.character_id,
                              created_at = EXCLUDED.created_at;
            kept := kept + 1;
        END LOOP;
        RETURN kept;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT) FROM PUBLIC;
    """
)

SQL = IMPORT_PLAYS

ISOLATED_TABLES: tuple[str, ...] = ("import_plays",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_import_plays", "dmbot_import_plays(BIGINT, TEXT, JSONB, INT, TEXT)"),
)
