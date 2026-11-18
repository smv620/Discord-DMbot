"""0551_party_tick_lists

The website's "who can know this" lists show the current party, not every player character
(#4778; Supervisor's ruling on #4537, `docs/design/party_knowledge.md` §3 and rule 13.1).
"""

from __future__ import annotations

# "The party" is asked in one place, `memory.party_record.current_members`: every confirmed player
# character with an open stretch in `memory_party_spans` (0549), or with none at all. Three website
# doors still listed player characters by role, which is not the party:
# - `dmbot_web_player_characters` (0281): the tick list of "Who knows this?" (people a DM may say
#   know a connection or a name);
# - `dmbot_web_party_characters` (0535): the "Some know" picker;
# - `dmbot_web_knowers_data` (0309): its `playerCharacters`, the same list for the knowers page.
# Each is read back from the database as it is and its player-character condition widened by one
# clause, its anchor found exactly once (a drift stops the migration), as 0535 does. A character
# who has left drops out of the lists the DM CHOOSES from, never out of what it knows: the
# `knownBy` lists (0281) and the knowers table are untouched, because nothing a character knew is
# forgotten (§6), and a departed character who knows a connection still shows as knowing it.
# No table changes, no rows change, no new door.
_ALIVE = (
    "AND NOT (EXISTS (SELECT 1 FROM memory_party_spans ps\n"
    "                              WHERE ps.guild_id = {a}.guild_id\n"
    "                                AND ps.campaign_id = {a}.campaign_id\n"
    "                                AND ps.entity_id = {a}.id)\n"
    "                     AND NOT EXISTS (SELECT 1 FROM memory_party_spans ps\n"
    "                              WHERE ps.guild_id = {a}.guild_id\n"
    "                                AND ps.campaign_id = {a}.campaign_id\n"
    "                                AND ps.entity_id = {a}.id AND ps.to_at IS NULL))"
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


# (door, the text to find, what replaces it): each found exactly once.
_SWAPS = (
    (
        "dmbot_web_player_characters",
        "AND e.role = 'player_character' AND e.status = 'confirmed'",
        "AND e.role = 'player_character' AND e.status = 'confirmed'\n                    "
        + _ALIVE.format(a="e"),
    ),
    (
        "dmbot_web_party_characters",
        "AND e.role = 'player_character' AND e.status = 'confirmed'",
        "AND e.role = 'player_character' AND e.status = 'confirmed'\n                    "
        + _ALIVE.format(a="e"),
    ),
    (
        "dmbot_web_knowers_data",
        "AND x.role = 'player_character' AND x.status = 'confirmed'), '[]'::jsonb),",
        "AND x.role = 'player_character' AND x.status = 'confirmed'\n                  "
        + _ALIVE.format(a="x")
        + "), '[]'::jsonb),",
    ),
)

_STEPS = "\n".join(
    f"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace AND p.proname = '{door}';
        def := pg_temp.swap_once(def, {_dollar(old)}, {_dollar(new)});
        EXECUTE def;
    END
    $do$;"""
    for door, old, new in _SWAPS
)

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
"""
    + _STEPS
)

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
