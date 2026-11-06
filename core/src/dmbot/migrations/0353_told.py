"""0353_told

Telling one character that an other name is an entry (#3089).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

TOLD = r"""
    -- "Tavin now knows the guard is Ken" (#3089): the DM tells ONE character that a secret other
    -- name is an entry, without making the name public. One row for each (other name, character).
    -- It is not a fact about the story, only about what one character was told, so it lives beside
    -- the knowers and never in them (a knower row means "knows this statement"; this means "has
    -- linked these two"). `added_entry`: telling also gave the character the entry itself (its name
    -- only, no facts), and taking the telling back removes that row only when this telling added
    -- it. Logged (Undo), carried in backups, gone with its name or character.
    CREATE TABLE memory_told (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        alias_id     TEXT NOT NULL,
        character_id TEXT NOT NULL,
        added_entry  BOOLEAN NOT NULL,
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, alias_id, character_id),
        FOREIGN KEY (guild_id, campaign_id, alias_id)
            REFERENCES memory_aliases (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, character_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (the character key is how a deleted or merged character finds its tellings)
    CREATE INDEX memory_told_by_character ON memory_told (guild_id, campaign_id, character_id);

    -- Who was told what, for the API to put through the gate (`memory/gate.py`): the other name
    -- and the character, ids only. The DM is given every row; a member who is not the DM is given
    -- only the rows of their own characters, worked out here in SQL from who plays them and nothing
    -- the request says, so one player never learns what another's character was told. Members of
    -- the server only, as the other graph doors: anyone else gets NULL. Nothing is written.
    CREATE FUNCTION dmbot_web_told(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        RETURN jsonb_build_object('status', 'ok', 'told', COALESCE((
            SELECT jsonb_agg(jsonb_build_array(t.alias_id, t.character_id)
                             ORDER BY t.alias_id, t.character_id)
            FROM memory_told t
            WHERE t.guild_id = g AND t.campaign_id = c
              AND (is_dm OR t.character_id IN (
                    SELECT x.id FROM memory_entities x
                    WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'
                      AND x.role = 'player_character' AND x.status = 'confirmed'
                      AND x.merged_into IS NULL AND dmbot_current_user() IS NOT NULL
                      AND x.played_by = dmbot_current_user()))), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_told(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_told(BIGINT, TEXT) FROM PUBLIC;
    """ + _isolate("memory_told")

SQL = TOLD

ISOLATED_TABLES: tuple[str, ...] = ("memory_told",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_told", "dmbot_web_told(BIGINT, TEXT)"),
)
