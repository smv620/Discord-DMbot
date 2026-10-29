"""0186_web_graph_views

Players / Full View and the peek notice (#2404).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _door__2,
    _isolate,
)

# Players View / Full View on the relationship web, and the peek notice (#2404, slice 1).
# The web API says which view it wants through a transaction setting (`dmbot.web_view`: 'players'
# or 'full'; nothing means the person's own: full for a DM, players for anyone else). The door
# re-checks it every time, so the setting only ever asks for less than the person has, or for a
# peek the campaign's DM-screen visibility allows ('peek' or 'open'); a peek under 'private' is
# refused ('peek_off'). A DM in Players View sees exactly what a player sees, and cannot edit.
# `web_peeks` holds the once-for-each-visit notice the bot posts on the DM screen ("<name> peeked
# at the relationship web"); a visit is one website sign-in. Isolated per server, gone with the
# campaign, never read by the website's role except through the peek door, and left out of backups.
WEB_GRAPH_VIEWS = (
    """
    CREATE TABLE web_peeks (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        visit       TEXT NOT NULL CHECK (char_length(visit) BETWEEN 1 AND 100),
        name        TEXT NOT NULL CHECK (char_length(name) <= 100),
        created_at  BIGINT NOT NULL,
        posted_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, user_id, visit),
        FOREIGN KEY (guild_id, campaign_id) REFERENCES campaigns (guild_id, id)
            ON DELETE CASCADE
    );
    CREATE INDEX web_peeks_waiting ON web_peeks (guild_id, created_at) WHERE posted_at IS NULL;
    """
    + _isolate("web_peeks")
    + r"""
    -- Note a peek: the caller's name and visit, once. Internal (no grant): the peek door and the
    -- graph door call it, so a full view is never given to a player without the DM being told,
    -- whichever way it was asked for. TRUE the first time for this visit.
    CREATE FUNCTION dmbot_web_peek_note(g BIGINT, c TEXT, v TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        who TEXT;
        added INT;
    BEGIN
        IF v IS NULL OR v = '' OR char_length(v) > 100 THEN
            RETURN NULL;
        END IF;
        SELECT left(COALESCE(
                   NULLIF(btrim(regexp_replace(display_name, '[[:space:]]+', ' ', 'g')), ''),
                   'Someone'), 100) INTO who
            FROM web_users WHERE user_id = dmbot_current_user();
        INSERT INTO web_peeks (guild_id, campaign_id, user_id, visit, name, created_at)
            VALUES (g, c, dmbot_current_user(), v, COALESCE(who, 'Someone'),
                    floor(extract(epoch FROM clock_timestamp()))::BIGINT)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        RETURN added > 0;
    END
    $fn$;

    -- Peeked: the caller (not a DM of the campaign) opens the full view for this visit. 'ok' with
    -- `noted` true the first time for this visit; 'peek_off' under a private DM screen. NULL when
    -- the caller is not in the server or there is no such campaign.
    CREATE FUNCTION dmbot_web_peek(g BIGINT, c TEXT, v TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        vis TEXT;
        noted BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT dm_screen_visibility INTO vis FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF EXISTS (SELECT 1 FROM campaign_dms d WHERE d.guild_id = g AND d.campaign_id = c
                                                  AND d.user_id = dmbot_current_user()) THEN
            RETURN jsonb_build_object('status', 'ok', 'noted', false);  -- the DM needs no notice
        END IF;
        IF vis NOT IN ('peek', 'open') THEN
            RETURN jsonb_build_object('status', 'peek_off');
        END IF;
        noted := dmbot_web_peek_note(g, c, v);
        IF noted IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_visit');
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'noted', noted);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_peek(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_peek_note(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_peek(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_peek_note(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

WEB_GRAPH_VIEWS = WEB_GRAPH_VIEWS + _door__2

SQL = WEB_GRAPH_VIEWS

ISOLATED_TABLES: tuple[str, ...] = ("web_peeks",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_peek", "dmbot_web_peek(BIGINT, TEXT, TEXT)"),
)
