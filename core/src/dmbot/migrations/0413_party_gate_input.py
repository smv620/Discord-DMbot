"""0413_party_gate_input

The website's gate input leaves the party out of its member list (#3977, a follow-up of #3976).
"""

from __future__ import annotations

# The party is one group entry, marked by `memory_entities.party`, and is never a knower
# (STORY_MEMORY 9b, #3954). The bot's gate (`memory/gate.load`) drops a group from the member
# list when it is the party, so a row that was on the party before that rule never reaches its
# members. The website builds the same gate input from `dmbot_web_knowers_data`, whose `groups`
# (member -> group) did not say which group was the party. Now a member_of connection to the
# party is not in the list, so the website and Discord agree. Only that one list changes; no row
# is changed. The door is read back from the database as it is (0311 rebuilt it) and the anchor
# must be found exactly once, so a drift stops the migration instead of editing the wrong line
# (as 0400 does).
_OLD = """WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'member_of'
                  AND r.status = 'confirmed'), '[]'::jsonb));"""
_NEW = """WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'member_of'
                  AND r.status = 'confirmed'
                  AND NOT EXISTS (SELECT 1 FROM memory_entities pe
                                  WHERE pe.guild_id = g AND pe.campaign_id = c
                                    AND pe.id = r.object_id AND pe.party)), '[]'::jsonb));"""


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
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
        def := pg_temp.swap_n(def, """
    + _dollar(_OLD)
    + ", "
    + _dollar(_NEW)
    + r""", 1);
        EXECUTE def;
    END
    $do$;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
