"""0281_web_who_knows

Who knows a held-back connection, for the website's DM (#2935).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _swaps,
)

# "Who knows this?" on the website (#2935; the core side is memory/knowers.py, 0279). A connection
# the DM keeps from the table can be known by some player characters: each holds it as a belief of
# its own at 1.0 (`memory_beliefs` on its `pc` point of view). The story graph's door now says
# which, to a DM in the full view only: never to a player, and never to a player who peeks behind
# the DM screen (they are `sees_all` but not a DM). The belief rule is `knowers.connection_knowers`:
# the newest belief of a point of view, at 1.0, on a connection that is still held back. A
# connection everyone knows has no knowers, so the page says "Everyone"; one with none says "Only
# the DM". The door is read back from the database as it is and widened, its anchor found exactly
# once (a drift stops the migration), as 0275 does. No table changes, no rows change.
_KNOWN_BY_EDGE_SWAPS = (
    (
        "'decided', CASE WHEN is_dm AND sees_all THEN r.decided END,",
        "'decided', CASE WHEN is_dm AND sees_all THEN r.decided END,\n"
        "                    'knownBy', CASE WHEN is_dm AND sees_all\n"
        "                                    AND (r.secret OR r.unrevealed) THEN COALESCE((\n"
        "                        SELECT jsonb_agg(jsonb_build_object('id', pe.entity_id,\n"
        "                                                            'name', en.name)\n"
        "                                         ORDER BY lower(en.name), pe.entity_id)\n"
        "                        FROM memory_perspectives pe\n"
        "                        JOIN memory_entities en ON en.guild_id = g\n"
        "                                               AND en.campaign_id = c\n"
        "                                               AND en.id = pe.entity_id\n"
        "                        WHERE pe.guild_id = g AND pe.campaign_id = c AND pe.kind = 'pc'\n"
        "                          AND (SELECT b.confidence FROM memory_beliefs b\n"
        "                               WHERE b.guild_id = g AND b.campaign_id = c\n"
        "                                 AND b.relation_id = r.id AND b.perspective_id = pe.id\n"
        "                               ORDER BY b.seq DESC LIMIT 1) >= 1.0), '[]'::jsonb) END,",
    ),
)

WEB_WHO_KNOWS = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_relationships';
"""
    + _swaps("def", _KNOWN_BY_EDGE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;

    -- The campaign's confirmed player characters (id and name, by name, at most 25: what one
    -- Discord menu holds, as `ui/who_knows.player_characters`): the only ones a DM may say know
    -- something. DM only; NULL for anyone else, a server the caller is not in, and a campaign that
    -- is not there.
    CREATE FUNCTION dmbot_web_player_characters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_user() IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object('id', x.id, 'name', x.name)
                             ORDER BY lower(x.name), x.id)
            FROM (SELECT e.id, e.name FROM memory_entities e
                  WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'character'
                    AND e.role = 'player_character' AND e.status = 'confirmed'
                    AND e.merged_into IS NULL
                  ORDER BY lower(e.name), e.id LIMIT 25) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_player_characters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_player_characters(BIGINT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_WHO_KNOWS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The campaign's player characters, for "Who knows this?" (#2935).
    ("dmbot_web_player_characters", "dmbot_web_player_characters(BIGINT, TEXT)"),
)
