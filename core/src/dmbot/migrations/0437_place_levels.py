"""0437_place_levels

How big a place is (#4173 slice 1): plane, world, region, settlement, district, street, building or
room, on an entry, in the All names door and as one more website action.
"""

from __future__ import annotations

# `place_level` on an entry: NULL when nobody knows (every existing row, and any place whose words
# say no size), else one of the eight levels of `memory/place_levels.py`, largest first. The lore
# reader sets it from the name or the words beside it; the DM sets or changes it in one tap. Only a
# place has one (the store keeps that rule, as it does `unique_item` for items; a damaged backup
# says so). `place_level_from` says who gave it: 'read' (DMbot, from the words) or 'dm' (the DM's
# tap), set together with the level and NULL with it, so a size DMbot read is always shown as its
# read and never as a fact (the DM's word wins a merge). No existing row changes, so no row-level
# security needs opening. Both are carried by the change log, merges and backups.
#
# The website, level with Discord's 📏 Size menu: the All names door (`dmbot_web_all_names`) sends
# each entry's `placeLevel` and `placeLevelFrom` ('read' or 'dm') to whoever sees the name (a size
# is no secret: the name's own visibility rules hold it), and the queue door accepts `place-size`,
# whose text is one level word, or blank for "Not sure"; no target, no flag. The bot runs it through
# `MemoryStore.set_place_level`, which refuses an entry that is not a place. Both doors are read
# back from the database as they are and each anchor must be found the exact number of times, so a
# drift stops the migration instead of widening the wrong rule (as 0358 and 0434 do). The anchors
# are lines no open migration changes.
LEVELS = ("plane", "world", "region", "settlement", "district", "street", "building", "room")

_ALL_NAMES_SWAPS = (
    # the column, carried through the visible rows
    (
        "                   e.players_text,\n",
        "                   e.players_text, e.place_level, e.place_level_from,\n",
        1,
    ),
    # sent with each name
    (
        "'playersText', CASE WHEN is_dm THEN p.players_text END,\n",
        "'playersText', CASE WHEN is_dm THEN p.players_text END,\n"
        "                    'placeLevel', p.place_level, 'placeLevelFrom', p.place_level_from,\n",
        1,
    ),
)

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'spelling-yes', 'spelling-no', 'players-line', 'offer-clear',\n",
        "'spelling-yes', 'spelling-no', 'players-line', 'offer-clear', 'place-size',\n",
        1,
    ),
    # the text is one level word, or blank to clear it
    (
        "           OR (act = 'confirm-undo' AND clean <> '')\n",
        "           OR (act = 'confirm-undo' AND clean <> '')\n"
        f"           OR (act = 'place-size' AND clean !~ '^({'|'.join(LEVELS)})?$')\n",
        1,
    ),
    # no flag
    (
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear')\n"
        "               AND COALESCE(fl, false)) THEN",
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear', 'place-size')\n"
        "               AND COALESCE(fl, false)) THEN",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
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
        + door
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = (
    f"""
    ALTER TABLE memory_entities ADD COLUMN place_level TEXT
        CHECK (place_level IN ({", ".join(f"'{w}'" for w in LEVELS)}));
    ALTER TABLE memory_entities ADD COLUMN place_level_from TEXT
        CHECK (place_level_from IN ('read', 'dm'));
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_place_level_pair
        CHECK ((place_level IS NULL) = (place_level_from IS NULL));
"""
    + r"""
    -- The allowed actions, read back from the table as it is and one more added at the end, so an
    -- action another migration added stays in the list whatever the order the migrations merge in.
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
            || replace(d, '])', ', ''place-size''::text])');
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
    + _rebuild("dmbot_web_all_names", _ALL_NAMES_SWAPS)
    + _rebuild("dmbot_web_name_action_add", _QUEUE_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
