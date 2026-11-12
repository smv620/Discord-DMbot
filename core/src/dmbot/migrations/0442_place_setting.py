"""0442_place_setting

The campaign's setting (#4173 slice 2a): one place entry marked as where the whole story is
(Faerûn, or the Material Plane), on the All names door and as one more website action.
"""

from __future__ import annotations

# `memory_entities.setting`: at most one entry per campaign is the setting (an index, as the party
# has, 0117); the store keeps it to a confirmed place no smaller than a region. A place with no
# stated parent and a known smaller size counts as inside it when DMbot checks where something is
# (`checks.Nesting`), so "Is X in Faerûn?" is never asked; nothing is stored for that (the
# "assumed" mark for the readers' chain is slice 3's). Defaults to false, so no existing row
# changes and no row-level security needs opening.
#
# The website, level with Discord's 🌍 button: the All names door sends `setting` with each name it
# shows (a setting is no secret: the name's own visibility rules hold it), and the queue door
# accepts `place-setting`, with the flag true to make the place the setting and false to clear it;
# no text, no target. The bot runs it through `MemoryStore.set_setting`, which holds the rules.
# Both doors are read back as they are and each anchor must be found the exact number of times
# (as 0437 does); the anchors are 0437's own lines, which no other open migration changes.
_ALL_NAMES_SWAPS = (
    (
        "                   e.players_text, e.place_level, e.place_level_from,\n",
        "                   e.players_text, e.place_level, e.place_level_from, e.setting,\n",
        1,
    ),
    (
        "'placeLevel', p.place_level, 'placeLevelFrom', p.place_level_from,\n",
        "'placeLevel', p.place_level, 'placeLevelFrom', p.place_level_from,\n"
        "                    'setting', p.setting,\n",
        1,
    ),
)

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'players-line', 'offer-clear', 'place-size',\n",
        "'players-line', 'offer-clear', 'place-size', 'place-setting',\n",
        1,
    ),
    # a flag, and no text
    (
        "           OR (act = 'place-size' AND clean !~ ",
        "           OR (act = 'place-setting' AND (clean <> '' OR fl IS NULL))\n"
        "           OR (act = 'place-size' AND clean !~ ",
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
    r"""
    ALTER TABLE memory_entities ADD COLUMN setting BOOLEAN NOT NULL DEFAULT false;
    CREATE UNIQUE INDEX memory_entities_one_setting
        ON memory_entities (guild_id, campaign_id) WHERE setting;

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
            || replace(d, '])', ', ''place-setting''::text])');
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
