"""0553_knowers_left_why

The website's knowers door says why each player character who left the party left (#4778;
Supervisor's ruling on #4537, `docs/design/party_knowledge.md` §7).
"""

from __future__ import annotations

# The website words "who knows it" with core's own `party_icons.line`, whose 🤫 line names who else
# knows it, characters who left first, each with why ("Tavin (retired)"). `dmbot_web_knowers_data`
# already lists the leavers (`leftParty`, 0551); this adds `leftWhy`, a map from each leaver's id to
# the word of their last ended stretch (left, retired, died or away). The door is read back as it is
# and its anchor found exactly once (a drift stops the migration), as 0551 does. No table changes,
# no rows change, no new door; grants and search paths are unchanged. It also adds `partyOrder`, the
# campaign's confirmed player characters oldest first (`party_record.player_characters`' order), so
# the website lists the party in Discord's order ("Known to: A and B"); `playerCharacters` stays
# in id order, which nothing about the party depends on.
_ANCHOR = "-- one row for each (statement, character): kind, statement id, character, certainty"
_ORDER = (
    "'partyOrder', COALESCE((\n"
    "                SELECT jsonb_agg(x.id ORDER BY x.created_at, x.id) FROM memory_entities x\n"
    "                WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'\n"
    "                  AND x.role = 'player_character' AND x.status = 'confirmed'),\n"
    "                '[]'::jsonb),\n            "
)
_WHY = (
    "'leftWhy', COALESCE((\n"
    "                SELECT jsonb_object_agg(w.entity_id, w.ended) FROM (\n"
    "                    SELECT DISTINCT ON (ps.entity_id) ps.entity_id, ps.ended\n"
    "                    FROM memory_party_spans ps\n"
    "                    WHERE ps.guild_id = g AND ps.campaign_id = c AND ps.to_at IS NOT NULL\n"
    "                      AND NOT EXISTS (SELECT 1 FROM memory_party_spans o\n"
    "                                      WHERE o.guild_id = ps.guild_id\n"
    "                                        AND o.campaign_id = ps.campaign_id\n"
    "                                        AND o.entity_id = ps.entity_id AND o.to_at IS NULL)\n"
    "                    ORDER BY ps.entity_id, ps.to_at DESC, ps.from_at DESC) w),\n"
    "                '{}'::jsonb),\n            "
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
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
              AND p.proname = 'dmbot_web_knowers_data';
        def := pg_temp.swap_once(def, """
    + _dollar(_ANCHOR)
    + ", "
    + _dollar(_ORDER + _WHY + _ANCHOR)
    + r""");
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
