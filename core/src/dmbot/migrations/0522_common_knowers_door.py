"""0522_common_knowers_door

The website's who-knows door gives common knowledge too (#4546, #4537 slice 1a, web half).
"""

from __future__ import annotations

# 0513 gave each campaign one more point of view, kind `everyone` (no entry of its own: its
# `memory_perspectives.entity_id` is NULL), and a `memory_knowers` row held by it is common
# knowledge: every character knows it, NPC or player character, now or later, unless the character
# has a row of their own. The bot's gate counts such a row only while the players are shown the
# statement (`gate.common_while_shown`). The website builds the same gate input from
# `dmbot_web_knowers_data` (0309, 0311), which listed only rows of a character's own point of view
# (`p.entity_id IS NOT NULL`), so a name every player character knows read as "only the DM" or
# "some" there. Now the knowers list also carries the everyone rows, as the character `@everyone`
# (`gate.COMMON`), and only for a statement the players are shown by its own flags, worked out here
# from the same columns the bot reads (`knowers.entry_shown`, `relation_shown`, `alias_shown`,
# `entry_shown_end`):
# - an entry: confirmed, and not hidden from the players, limited to an audience, or unnamed;
# - a property: its entry is shown;
# - a connection: confirmed, neither secret nor not yet revealed, and both its ends shown;
# - an other name: not rejected and not secret, its entry shown, and the entry it is used by, if
#   any, shown.
# A row a hide put to sleep stays out, as for every other row. The `learned` list keeps its
# character-only rows (an everyone row has no time of learning to give). Counts and ids only, as
# before; the door's answer is read back from the database as it is and each anchor must be found
# exactly once, so a drift stops the migration. No row is changed, and `CREATE OR REPLACE` keeps
# the function's pinned search path and its grants, as 0413 relies on.


def _shown(entry: str) -> str:
    """`entry` (a SQL id expression) names an entry the players are shown."""
    return (
        "EXISTS (SELECT 1 FROM memory_entities xe WHERE xe.guild_id = g AND xe.campaign_id = c "
        f"AND xe.id = {entry} AND xe.status = 'confirmed' "
        "AND NOT (xe.hidden_from_players OR xe.unnamed OR xe.audience_limited))"
    )


def _end_shown(entry: str) -> str:
    """The same for an end of a connection (`entry_shown_end`: no status test)."""
    return (
        "EXISTS (SELECT 1 FROM memory_entities xe WHERE xe.guild_id = g AND xe.campaign_id = c "
        f"AND xe.id = {entry} "
        "AND NOT (xe.hidden_from_players OR xe.unnamed OR xe.audience_limited))"
    )


_COMMON_SHOWN = (
    "(p.kind = 'everyone' AND ("
    f"(k.about_entity_id IS NOT NULL AND {_shown('k.about_entity_id')}) "
    "OR (k.state_id IS NOT NULL AND EXISTS (SELECT 1 FROM memory_states xs "
    "WHERE xs.guild_id = g AND xs.campaign_id = c AND xs.id = k.state_id "
    f"AND {_shown('xs.entity_id')})) "
    "OR (k.relation_id IS NOT NULL AND EXISTS (SELECT 1 FROM memory_relations xr "
    "WHERE xr.guild_id = g AND xr.campaign_id = c AND xr.id = k.relation_id "
    "AND xr.status = 'confirmed' AND NOT (xr.secret OR xr.unrevealed) "
    f"AND {_end_shown('xr.subject_id')} AND {_end_shown('xr.object_id')})) "
    "OR (k.alias_id IS NOT NULL AND EXISTS (SELECT 1 FROM memory_aliases xa "
    "WHERE xa.guild_id = g AND xa.campaign_id = c AND xa.id = k.alias_id "
    "AND xa.status <> 'rejected' AND NOT xa.secret "
    f"AND {_shown('xa.entity_id')} "
    f"AND (xa.used_by IS NULL OR {_shown('xa.used_by')})))))"
)

_SWAPS = (
    # the character of a row: an everyone row has none, so it is the gate's common key
    (
        "COALESCE(k.about_entity_id, k.state_id, k.relation_id, k.alias_id),\n"
        "                           p.entity_id, k.confidence))",
        "COALESCE(k.about_entity_id, k.state_id, k.relation_id, k.alias_id),\n"
        "                           COALESCE(p.entity_id, '@everyone'), k.confidence))",
        1,
    ),
    # which rows: a character's own, and the everyone rows the players are shown
    (
        "                  AND p.entity_id IS NOT NULL), '[]'::jsonb),\n            'liveEntries'",
        f"                  AND (p.entity_id IS NOT NULL OR {_COMMON_SHOWN})), '[]'::jsonb),\n"
        "            'liveEntries'",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
)

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
              AND p.proname = 'dmbot_web_knowers_data';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
