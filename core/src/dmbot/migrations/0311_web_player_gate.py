"""0311_web_player_gate

The website's story graph for a player is worked out by the one gate, from a source door (#3143).
"""

from __future__ import annotations

import importlib

# 0309's knowers door is rebuilt from that file's own text, so the two cannot drift apart; each
# replacement is asserted, so a change to the original that moves a spot fails here instead of
# quietly dropping it.
_OLD = importlib.import_module("dmbot.migrations.0309_web_knowers_data")


def _once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


_KNOWERS = _OLD.SQL
_KNOWERS = _once(
    _KNOWERS,
    "DM only: anyone else, and a campaign that is not\n    -- theirs, gets NULL.",
    "Every member of the server may ask, and a campaign\n"
    "    -- that is not here gets NULL; only a DM is sent the characters to pick from.",
)
_KNOWERS = _once(
    _KNOWERS,
    "CREATE FUNCTION dmbot_web_knowers_data(g BIGINT, c TEXT) RETURNS JSONB\n"
    "    LANGUAGE plpgsql SECURITY DEFINER AS $fn$\n    BEGIN\n",
    "CREATE OR REPLACE FUNCTION dmbot_web_knowers_data(g BIGINT, c TEXT) RETURNS JSONB\n"
    "    LANGUAGE plpgsql SECURITY DEFINER AS $fn$\n    DECLARE\n"
    "        is_dm BOOLEAN;\n    BEGIN\n",
)
_KNOWERS = _once(
    _KNOWERS,
    "           OR NOT EXISTS (SELECT 1 FROM campaign_dms d\n"
    "                          WHERE d.guild_id = g AND d.campaign_id = c\n"
    "                            AND d.user_id = dmbot_current_user()) THEN\n"
    "            RETURN NULL;\n        END IF;\n",
    "           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN\n"
    "            RETURN NULL;\n        END IF;\n"
    "        is_dm := EXISTS (SELECT 1 FROM campaign_dms d\n"
    "                         WHERE d.guild_id = g AND d.campaign_id = c\n"
    "                           AND d.user_id = dmbot_current_user());\n",
)
_KNOWERS = _once(
    _KNOWERS,
    "            'status', 'ok',\n",
    "            'status', 'ok', 'isDm', is_dm,\n"
    "            -- the caller's own characters, from who plays them and nothing the request says\n"
    "            'mine', COALESCE((\n"
    "                SELECT jsonb_agg(x.id ORDER BY x.id) FROM memory_entities x\n"
    "                WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'\n"
    "                  AND x.role = 'player_character' AND x.status = 'confirmed'\n"
    "                  AND x.merged_into IS NULL AND dmbot_current_user() IS NOT NULL\n"
    "                  AND x.played_by = dmbot_current_user()), '[]'::jsonb),\n",
)
_KNOWERS = _once(
    _KNOWERS,
    "                        AND x.status = 'confirmed' AND x.merged_into IS NULL\n"
    "                      ORDER BY (x.role",
    "                        AND x.status = 'confirmed' AND x.merged_into IS NULL AND is_dm\n"
    "                      ORDER BY (x.role",
)
_KNOWERS = _once(
    _KNOWERS,
    "'secret', a.secret) ORDER BY a.created_at, a.id)",
    "'secret', a.secret, 'status', a.status) ORDER BY a.created_at, a.id)",
)

WEB_PLAYER_GATE = (
    r"""
    -- Who may look at the story graph through the gate (#3143). The graph a player sees used to be
    -- worked out here in SQL from the old table-wide flags, OR'd with what their characters know
    -- (0294): two rules for one question. Now there is one: the website's API reads this source,
    -- runs the gate (`memory.gate`) for the viewer, and sends the browser only what the gate lets
    -- that viewer see. So the knowers door below answers every member of the server (it still
    -- sends the list of characters to pick from to a DM only), with the caller's own characters
    -- taken from who plays them; and the source door sends the API every name and every
    -- confirmed connection. Neither is ever sent to a browser, neither writes a row.
"""
    + _KNOWERS
    + r"""

    -- The graph's source: every confirmed name and connection of one campaign, for the API to
    -- filter through the gate. Members of the server only, as the graph doors were.
    CREATE FUNCTION dmbot_web_graph_source(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        screen TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT dm_screen_visibility INTO screen FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok', 'visibility', screen,
            'isDm', EXISTS (SELECT 1 FROM campaign_dms d
                            WHERE d.guild_id = g AND d.campaign_id = c
                              AND d.user_id = dmbot_current_user()),
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'name', e.name,
                           'kind', CASE WHEN e.type = 'character' THEN
                                       CASE e.role WHEN 'player_character' THEN 'player_character'
                                                   WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                                   ELSE 'character' END
                                   ELSE e.type END) ORDER BY e.id)
                FROM (SELECT * FROM memory_entities x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'
                        AND x.merged_into IS NULL AND x.type <> 'person'
                        AND x.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                           'promise', 'deed')
                      ORDER BY x.id LIMIT 5000) e), '[]'::jsonb),
            'links', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', r.id, 'a', r.subject_id, 'b', r.object_id,
                           'predicate', r.predicate, 'detail', r.detail,
                           'directional', r.directional, 'secret', r.secret,
                           'unrevealed', r.unrevealed, 'at', r.created_at,
                           'via', r.via_alias_id,
                           'ended', r.to_session_at IS NOT NULL,
                           'plan', r.source IN ('plan', 'story_plan')) ORDER BY r.created_at, r.id)
                FROM (SELECT * FROM memory_relations x
                      WHERE x.guild_id = g AND x.campaign_id = c AND x.status = 'confirmed'
                        AND x.predicate <> 'led_to'
                      ORDER BY x.created_at, x.id LIMIT 20000) r), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_knowers_data(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_source(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_knowers_data(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_graph_source(BIGINT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_PLAYER_GATE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The graph's source for the API to filter through the gate (#3143). The knowers door was
    # registered by 0309 and keeps its place.
    ("dmbot_web_graph_source", "dmbot_web_graph_source(BIGINT, TEXT)"),
)
