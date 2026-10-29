"""0138_member_directory

Who an imported name can be (#2141).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

MEMBER_DIRECTORY = (
    """
    -- Who a name in an imported transcript can be tied to (#2141): the members of a server who
    -- agreed to be recorded, with the display name and Discord username the bot last saw. Their
    -- names already appear in the server's shared transcripts, so this keeps nothing new about
    -- them. It is kept per server (never read across servers), written only by the bot, and a row
    -- is dropped when the person stops being recorded. The import search shows a row only while
    -- the person's consent is current, so a stale row is never offered.
    CREATE TABLE member_directory (
        guild_id     BIGINT NOT NULL,
        user_id      BIGINT NOT NULL CHECK (user_id > 0),
        display_name TEXT NOT NULL DEFAULT '' CHECK (char_length(display_name) <= 100),
        username     TEXT NOT NULL DEFAULT '' CHECK (char_length(username) <= 100),
        updated_at   BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("member_directory")
    + """
    -- The import page's member search, now also over the directory: the campaign's people first,
    -- then the server's recorded members by name or username, at most 25. A name from the
    -- directory never beats a campaign person who plays the same account. NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_members(
        g BIGINT, c TEXT, q TEXT, terms INT, outside TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        found JSONB;
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
        SELECT COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', m.user_id::TEXT, 'name', m.name,
                       'agreed', EXISTS (SELECT 1 FROM consent x
                                         WHERE x.guild_id = g AND x.user_id = m.user_id
                                           AND x.terms_version = terms
                                           AND (outside IS NULL OR x.outside_to = outside)))
                       ORDER BY (lower(m.name) = lower(q)) DESC, m.rank, lower(m.name))
            FROM (SELECT one.name, one.user_id, one.rank FROM (
                      -- One row for each account: the campaign's name for the person if there is
                      -- one, else the directory's, else the website's. A search matches any of
                      -- the account's names, so it finds the same person whichever it hits.
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
                          SELECT d.user_id,
                                 CASE WHEN d.display_name <> '' THEN d.display_name
                                      ELSE d.username END,
                                 1, d.display_name || ' ' || d.username
                          FROM member_directory d
                          WHERE d.guild_id = g AND (d.display_name <> '' OR d.username <> '')
                          UNION ALL
                          SELECT u.user_id, u.display_name, 2, u.display_name
                          FROM web_users u
                          WHERE coalesce(u.display_name, '') <> ''
                      ) n
                      WHERE EXISTS (SELECT 1 FROM consent x
                                    WHERE x.guild_id = g AND x.user_id = n.user_id
                                      AND x.terms_version = terms
                                      AND (outside IS NULL OR x.outside_to = outside))
                         OR n.rank = 0
                      GROUP BY n.user_id
                  ) one
                  WHERE q = '' OR position(lower(q) IN one.hay) > 0
                  ORDER BY (lower(one.name) = lower(q)) DESC, one.rank, lower(one.name)
                  LIMIT 25) m), '[]'::jsonb) INTO found;
        PERFORM set_config('dmbot.import_names', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_import_members(BIGINT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;
    """
)

SQL = MEMBER_DIRECTORY

ISOLATED_TABLES: tuple[str, ...] = ("member_directory",)
