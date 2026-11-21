"""0587_web_node_traits

The website shows a character's lineage and class to the DM, as Discord's name card does
(#3925, the web half of #2959 item 3).
"""

from __future__ import annotations

# `dmbot_web_node_traits(g, c, e)`: NULL unless the caller is a DM of the campaign (0404's check).
# Else the rules links `memory_rule_links` keeps for that one character (kinds `species` and
# `class` only: the card's two), `[{kind, name, known, edition}]`, the species first (as the
# card's line reads), then by name, and `[]` for an entry that is not a character the DM keeps
# (confirmed or waiting, not merged). The rows are the card's (`MemoryStore.rule_links`); the page
# words the line, with the 2014 tag and "(not in DMbot's rules)", so the two cannot drift. DM only
# because a spoilers upload may give a lineage the players should not see. Names and flags only:
# no line of lore. No table, no action.

SQL = r"""
    CREATE FUNCTION dmbot_web_node_traits(g BIGINT, c TEXT, e TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'kind', l.kind, 'name', l.name, 'known', l.known,
                       'edition', COALESCE(l.edition, '')) ORDER BY (l.kind <> 'species'), l.name)
            FROM memory_rule_links l
            JOIN memory_entities x ON x.guild_id = l.guild_id AND x.campaign_id = l.campaign_id
                                  AND x.id = l.entity_id AND x.type = 'character'
                                  AND x.status IN ('confirmed', 'proposed')
                                  AND x.merged_into IS NULL
            WHERE l.guild_id = g AND l.campaign_id = c AND l.entity_id = e
              AND l.kind IN ('species', 'class')), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_node_traits(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_node_traits(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_node_traits", "dmbot_web_node_traits(BIGINT, TEXT, TEXT)"),
)
