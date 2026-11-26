"""0590_web_place_culture

The website shows and sets a place's culture and who lives there (#4412, the web half of #4257
slices 3a and 3b), level with Discord's "🏛️ Culture, who lives here".
"""

from __future__ import annotations

# Two layers of `memory_map_facts`, one row per lineage side by side (`memory/place_peoples.py`).
# The doors that read a place's rows already return them; this adds what a page cannot work out
# alone: a row's NAMED culture (`culture_id`, then `value` is '': "Illuskan"), in both place doors
# (`dmbot_web_place_world` for the DM, `dmbot_web_place_world_table` for the table: the table door
# still gives only rows the DM confirmed and showed, so players see a culture's name only then),
# and the campaign's kept named cultures for the picker (at most 14, the DM's door only).
#
# Two actions go on the queue door, filed under the place like `map-terrain`: `map-culture` and
# `map-population`, whose text is `<lineage> <how>`, `<culture id> <how>` (culture only) or
# `other <how> <the DM's words>`, with `-` for no how (a population's "not said"), or nothing to
# clear the layer (every lineage). `how` is one of `strong|some|traces|once` for a culture and
# `all|most|many|some|few` or `-` for who lives here. The bot runs `MemoryStore.set_map_fact`, the
# call Discord's pickers make, which checks the rest; words are at most 60 characters. The queue
# door's lists and shape check are read back as they are and each anchor must be found exactly the
# number of times given, so a drift stops the migration. No row is changed.
ACTIONS = ("map-culture", "map-population")
_LINEAGES = "dragonborn|dwarf|elf|gnome|goliath|halfling|human|orc|tiefling"
_ID = "[0-9a-f]{32}"
_CULTURE_HOW = "strong|some|traces|once"
_POPULATION_HOW = "-|all|most|many|some|few"

_QUEUE_SWAPS = (
    # the allowed actions
    ("'map-control', 'map-show'", "'map-control', 'map-culture', 'map-population', 'map-show'", 1),
    # the actions that carry no flag
    (
        "'map-terrain', 'map-climate', 'map-control')",
        "'map-terrain', 'map-climate', 'map-control', 'map-culture', 'map-population')",
        1,
    ),
    # their text
    (
        "           OR (act = 'map-show' AND (clean <> '' OR fl IS NULL))\n",
        f"           OR (act = 'map-culture' AND clean !~ "
        f"'^(({_LINEAGES}|{_ID}) ({_CULTURE_HOW})|other ({_CULTURE_HOW}) .{{1,60}})?$')\n"
        f"           OR (act = 'map-population' AND clean !~ "
        f"'^(({_LINEAGES}) ({_POPULATION_HOW})|other ({_POPULATION_HOW}) .{{1,60}})?$')\n"
        "           OR (act = 'map-show' AND (clean <> '' OR fl IS NULL))\n",
        1,
    ),
)

_DM_DOOR_SWAPS = (
    (
        "'how', f.how, 'part', f.part, 'status', f.status,",
        "'cultureId', f.culture_id,"
        " 'culture', (SELECT cu.name FROM memory_cultures cu"
        " WHERE cu.guild_id = g AND cu.campaign_id = c AND cu.id = f.culture_id"
        " AND cu.status <> 'rejected'),"
        " 'how', f.how, 'part', f.part, 'status', f.status,",
        1,
    ),
    (
        "            'groups', COALESCE((",
        "            'cultures', COALESCE(("
        " SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name)"
        " ORDER BY lower(t.name), t.id)"
        " FROM (SELECT cu.id, cu.name FROM memory_cultures cu"
        " WHERE cu.guild_id = g AND cu.campaign_id = c AND cu.status = 'confirmed'"
        " ORDER BY lower(cu.name), cu.id LIMIT 14) t), '[]'::jsonb),\n"
        "            'groups', COALESCE((",
        1,
    ),
)

_TABLE_DOOR_SWAPS = (
    (
        "'how', f.how, 'part', f.part)",
        "'culture', (SELECT cu.name FROM memory_cultures cu"
        " WHERE cu.guild_id = g AND cu.campaign_id = c AND cu.id = f.culture_id"
        " AND cu.status <> 'rejected'),"
        " 'how', f.how, 'part', f.part)",
        1,
    ),
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made (each anchor found exactly the
    number of times given), and the new definition made."""
    steps = "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + function
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
"""
    )


SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and two more added at the end.
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_action_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the action list did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(d, '])', ', """
    + _ADDED
    + r"""])');
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
"""
    + _swap("dmbot_web_name_action_add", _QUEUE_SWAPS)
    + _swap("dmbot_web_place_world", _DM_DOOR_SWAPS)
    + _swap("dmbot_web_place_world_table", _TABLE_DOOR_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
