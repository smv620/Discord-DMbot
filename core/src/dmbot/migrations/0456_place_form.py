"""0456_place_form

A place's form (#4173 slice 6a): an area (the default), water (a lake, bay, sea or river) or a
route (a named road, trade route or pass), on an entry, in the All names door and as one more
website action.
"""

from __future__ import annotations

# Supervisor's ruling on #4173 (2026-11-11, slice 6 point 2): one `form` column replaces the water
# mark first approved. NULL is an area, every existing place and the default; 'water' is what "on
# the shore of" points at; 'route' is a named way the towns are "on". Water and route places stand
# outside the size order (`place_levels`), so being "on the Eastway" is never a rival of being in a
# town. `form_from` says who gave it, 'read' (DMbot, from the words) or 'dm' (the DM's tap), set
# together with the form and NULL with it, so a form DMbot read is always shown as read. Only a
# place has one (the store keeps the rule; a damaged backup says so). No existing row changes, so no
# row-level security needs opening. Both are carried by the change log, merges and backups.
#
# The website, level with Discord: the All names door (`dmbot_web_all_names`) sends `form` and
# `formFrom` with each name (no secret: the name's own visibility holds it), and the queue door
# accepts `place-form`, whose text is 'water', 'route', or blank for an area; no target, no flag.
# The bot runs it through `MemoryStore.set_place_form`. Both doors are read back from the database
# as they are and each anchor must be found exactly once, so a drift stops the migration (as 0437
# and 0442 do). The anchors are the lines 0442 left.
FORMS = ("water", "route")

_ALL_NAMES_SWAPS = (
    (
        "                   e.players_text, e.place_level, e.place_level_from, e.setting,\n",
        "                   e.players_text, e.place_level, e.place_level_from, e.setting,\n"
        "                   e.form, e.form_from,\n",
        1,
    ),
    (
        "                    'setting', p.setting,\n",
        "                    'setting', p.setting, 'form', p.form, 'formFrom', p.form_from,\n",
        1,
    ),
)

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'players-line', 'offer-clear', 'place-size', 'place-setting',\n",
        "'players-line', 'offer-clear', 'place-size', 'place-setting', 'place-form',\n",
        1,
    ),
    # the text is one form word, or blank for an area
    (
        "           OR (act = 'place-setting' AND (clean <> '' OR fl IS NULL))\n",
        "           OR (act = 'place-setting' AND (clean <> '' OR fl IS NULL))\n"
        f"           OR (act = 'place-form' AND clean !~ '^({'|'.join(FORMS)})?$')\n",
        1,
    ),
    # no flag
    (
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear', 'place-size')\n"
        "               AND COALESCE(fl, false)) THEN",
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear', 'place-size',\n"
        "                      'place-form')\n"
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
    ALTER TABLE memory_entities ADD COLUMN form TEXT
        CHECK (form IN ({", ".join(f"'{w}'" for w in FORMS)}));
    ALTER TABLE memory_entities ADD COLUMN form_from TEXT
        CHECK (form_from IN ('read', 'dm'));
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_form_pair
        CHECK ((form IS NULL) = (form_from IS NULL));
"""
    + r"""
    -- The allowed actions, read back from the table as it is and one more added at the end.
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
            || replace(d, '])', ', ''place-form''::text])');
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
