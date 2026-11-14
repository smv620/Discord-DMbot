"""0367_pc_icons

The Story Graph's player character icons (#3621): the DM's or the player's choice for each
character, a read door that resolves what to draw, and a write door that checks who may choose.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# No row means the default order (the player's Discord avatar, else the D&D Beyond portrait, else
# the page's own 👤). A row is a choice: `discord`, `dndbeyond`, `hidden` (no picture at all, for
# every viewer) or `custom` (a picture of the campaign's own; `custom_ref` names it; the website
# cannot set it yet, #3623). Only a choice and a time are stored: the avatar hash is the
# directory's (0366) and the portrait link is in the sheet snapshot, both read when asked, so a
# restored campaign never carries a stale picture. The choice is campaign data and goes in backups.
TABLE = r"""
    CREATE TABLE pc_icons (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id   TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        choice      TEXT NOT NULL CHECK (choice IN ('discord', 'dndbeyond', 'custom', 'hidden')),
        custom_ref  TEXT CHECK (custom_ref IS NULL OR custom_ref ~ '^[0-9a-f]{32}$'),
        set_by      BIGINT NOT NULL CHECK (set_by > 0),
        updated_at  BIGINT NOT NULL CHECK (updated_at > 0),
        CHECK ((choice = 'custom') = (custom_ref IS NOT NULL)),
        PRIMARY KEY (guild_id, campaign_id, entity_id)
    );
""" + _isolate("pc_icons")

# What each confirmed player character of the campaign draws, for any member of the server (the
# caller's page puts it only on nodes it already lets that person see, so a node they may not see
# never carries a picture). The avatar is built from the directory's hash for the PC's *current*
# player, so relinking changes it at once and a player who has not agreed has none; the portrait
# is the sheet snapshot's link, checked here again against D&D Beyond's own hosts. `canSet` is
# the DM or the PC's own player, worked out here from the signed-in user, and only they are told
# which pictures exist (`has`): that a player has no avatar stored can mean the player has not
# agreed, which the DM already sees on the consent list, and the player knows their own.
READ_DOOR = r"""
    CREATE FUNCTION dmbot_web_pc_icons(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', r.id,
                'choice', COALESCE(r.choice, 'default'),
                'icon', CASE
                    WHEN r.choice = 'hidden' THEN jsonb_build_object('kind', 'hidden', 'url', NULL)
                    WHEN r.choice = 'discord' THEN
                         CASE WHEN r.avatar_url IS NOT NULL THEN
                              jsonb_build_object('kind', 'discord', 'url', r.avatar_url) END
                    WHEN r.choice = 'dndbeyond' THEN
                         CASE WHEN r.portrait IS NOT NULL THEN
                              jsonb_build_object('kind', 'dndbeyond', 'url', r.portrait) END
                    WHEN r.choice IS NULL AND r.shown IS NOT NULL THEN
                         jsonb_build_object('kind', r.shown_kind, 'url', r.shown)
                    ELSE NULL END,
                'canSet', r.can_set)
                || CASE WHEN r.can_set THEN jsonb_build_object(
                       'has', jsonb_build_object('discord', r.avatar_url IS NOT NULL,
                                                 'dndbeyond', r.portrait IS NOT NULL))
                   ELSE '{}'::jsonb END
                ORDER BY r.id)
            FROM (
                SELECT e.id, i.choice, x.avatar_url, x.portrait,
                       COALESCE(x.avatar_url, x.portrait) AS shown,
                       CASE WHEN x.avatar_url IS NOT NULL THEN 'discord'
                            WHEN x.portrait IS NOT NULL THEN 'dndbeyond' END AS shown_kind,
                       (is_dm OR (e.played_by IS NOT NULL
                                  AND e.played_by = dmbot_current_user())) AS can_set
                FROM memory_entities e
                LEFT JOIN pc_icons i ON i.guild_id = e.guild_id AND i.campaign_id = e.campaign_id
                     AND i.entity_id = e.id
                LEFT JOIN LATERAL (
                    SELECT CASE WHEN d.avatar IS NOT NULL THEN
                                'https://cdn.discordapp.com/avatars/' || e.played_by || '/'
                                || d.avatar || '.png?size=128' END AS avatar_url,
                           CASE WHEN s.sheet->>'portrait' ~
                                '^https://(www\.dndbeyond\.com|media-waterdeep\.cursecdn\.com)/avatars/[A-Za-z0-9_./-]{1,200}$'
                                THEN s.sheet->>'portrait' END AS portrait
                    FROM (SELECT 1) one
                    LEFT JOIN member_directory d ON d.guild_id = e.guild_id
                         AND d.user_id = e.played_by
                    LEFT JOIN character_sheets s ON s.guild_id = e.guild_id
                         AND s.campaign_id = e.campaign_id AND s.entity_id = e.id
                ) x ON true
                WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'character'
                  AND e.role = 'player_character' AND e.status = 'confirmed'
                  AND e.merged_into IS NULL
                -- (500 is far above any table's party; past it the rest simply have no picture)
                ORDER BY e.id LIMIT 500) r
        ), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pc_icons(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pc_icons(BIGINT, TEXT) FROM PUBLIC;
"""

# The choice. `default` removes the row (Remove). The DM of the campaign or the PC's own player may
# choose, decided here from the signed-in user and never from the request; `discord` and
# `dndbeyond` only while that picture exists right now. Answers `{status, previous, now}` so the
# page's one Undo is simply writing `previous` back. Instant, one write: no queue and no wait.
SET_DOOR = r"""
    CREATE FUNCTION dmbot_web_pc_icon_set(g BIGINT, c TEXT, ent TEXT, pick TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        who BIGINT := dmbot_current_user();
        played BIGINT;
        before TEXT;
        has_discord BOOLEAN;
        has_portrait BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF pick IS NULL OR pick NOT IN ('default', 'discord', 'dndbeyond', 'hidden') THEN
            RETURN jsonb_build_object('status', 'bad_choice');
        END IF;
        SELECT e.played_by INTO played FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ent AND e.type = 'character'
              AND e.role = 'player_character' AND e.status = 'confirmed'
              AND e.merged_into IS NULL;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF NOT (EXISTS (SELECT 1 FROM campaign_dms d
                        WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who)
                OR (played IS NOT NULL AND played = who)) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        SELECT choice INTO before FROM pc_icons
            WHERE guild_id = g AND campaign_id = c AND entity_id = ent FOR UPDATE;
        IF pick = 'discord' THEN
            SELECT d.avatar IS NOT NULL INTO has_discord FROM member_directory d
                WHERE d.guild_id = g AND d.user_id = played;
            IF NOT COALESCE(has_discord, false) THEN
                RETURN jsonb_build_object('status', 'unavailable');
            END IF;
        ELSIF pick = 'dndbeyond' THEN
            SELECT s.sheet->>'portrait' ~
                   '^https://(www\.dndbeyond\.com|media-waterdeep\.cursecdn\.com)/avatars/[A-Za-z0-9_./-]{1,200}$'
                INTO has_portrait FROM character_sheets s
                WHERE s.guild_id = g AND s.campaign_id = c AND s.entity_id = ent;
            IF NOT COALESCE(has_portrait, false) THEN
                RETURN jsonb_build_object('status', 'unavailable');
            END IF;
        END IF;
        IF pick = 'default' THEN
            DELETE FROM pc_icons WHERE guild_id = g AND campaign_id = c AND entity_id = ent;
        ELSE
            INSERT INTO pc_icons (guild_id, campaign_id, entity_id, choice, set_by, updated_at)
                VALUES (g, c, ent, pick, who, now_s)
                ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET
                    choice = EXCLUDED.choice, custom_ref = NULL, set_by = EXCLUDED.set_by,
                    updated_at = EXCLUDED.updated_at;
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'previous', COALESCE(before, 'default'),
                                  'now', pick);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pc_icon_set(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pc_icon_set(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""

SQL = TABLE + READ_DOOR + SET_DOOR

ISOLATED_TABLES: tuple[str, ...] = ("pc_icons",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_pc_icons", "dmbot_web_pc_icons(BIGINT, TEXT)"),
    ("dmbot_web_pc_icon_set", "dmbot_web_pc_icon_set(BIGINT, TEXT, TEXT, TEXT)"),
)
