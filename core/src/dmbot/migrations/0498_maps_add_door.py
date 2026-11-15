"""0498_maps_add_door

A person sends a map from the website (#4347, slice B). The web API stages the picture's files in
the bucket and queues one `maps-add` action; the bot writes the rows as one Undo. This migration
opens the queue door for it, and for the poster's own take-back and Undo.
"""

from __future__ import annotations

# `maps-add` text: `<32 hex token> <png|jpg|webp> <64 hex sha256> <size> <preview size> <width>
# <height> <title>` (the staged files are `p/<token>.<ext>` and `p/<token>-preview.webp`; the title
# is the rest, 1 to 120 characters). The door checks only the shape, as it does for every action;
# the bot re-checks who sent it and that the two files are there, and deletes them when it refuses.
# Filed under no name, no target, no flag, like the other Maps actions (0487).
#
# Until now the queue door and its answer door were DM-only. A map may be sent by any of the
# campaign's people (a player's waits for a DM's yes), and the poster may take their own back, so
# the three actions `maps-add`, `maps-takeback` and `maps-undo` also pass a person who may read the
# campaign (`dmbot_web_reads_campaign`, 0489/0490: its DMs, its players, anyone heard at its table,
# or everyone when the channels are open to everyone). Every other action still needs a DM. The
# answer door only ever returns the caller's own rows, so it opens to the same people. The
# bot rechecks the sender for each action (a take-back is the poster's only; an Undo is read
# again among the sender's own done actions).
#
# The two door definitions are read back as they are (many migrations changed them) and each anchor
# must be found exactly once. A `maps-add` text gets the long text limit, in the door and in the
# table's own check. No row is changed.
_TOKEN = "[0-9a-f]{32}"
_SHAPE = (
    f"^{_TOKEN} (png|jpg|webp) [0-9a-f]{{64}} [0-9]{{1,9}} [0-9]{{1,9}} [0-9]{{1,5}} [0-9]{{1,5}}"
    " [^ ].{0,119}$"
)

_DM_ONLY = (
    "           OR NOT EXISTS (SELECT 1 FROM campaign_dms d\n"
    "                          WHERE d.guild_id = g AND d.campaign_id = c\n"
    "                            AND d.user_id = dmbot_current_user()) THEN"
)

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'maps-takeback', 'maps-undo')\n        THEN",
        "'maps-takeback', 'maps-undo', 'maps-add')\n        THEN",
        1,
    ),
    # its shape
    (
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'maps-undo' AND clean !~ '^[0-9]{1,15}$')\n"
        f"           OR (act = 'maps-add' AND clean !~ '{_SHAPE}')\n",
        1,
    ),
    # no flag
    (
        "'maps-takeback', 'maps-undo')\n               AND COALESCE(fl, false))",
        "'maps-takeback', 'maps-undo', 'maps-add')\n               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (
        "'maps-takeback', 'maps-undo') THEN",
        "'maps-takeback', 'maps-undo', 'maps-add') THEN",
        1,
    ),
    # the text may be as long as the longest action's
    (
        "'alias-who', 'confirm-kinds', 'world-keep')",
        "'alias-who', 'confirm-kinds', 'world-keep', 'maps-add')",
        1,
    ),
    # who may queue: a DM, or for these three actions anyone who may read the campaign
    (
        _DM_ONLY,
        "           OR NOT (EXISTS (SELECT 1 FROM campaign_dms d\n"
        "                           WHERE d.guild_id = g AND d.campaign_id = c\n"
        "                             AND d.user_id = dmbot_current_user())\n"
        "                   OR (COALESCE(act IN ('maps-add', 'maps-takeback', 'maps-undo'), false)\n"
        "                       AND dmbot_web_reads_campaign(g, c))) THEN",
        1,
    ),
)

# The answer door returns only the caller's own rows, so it opens to anyone who may read the
# campaign.
_READ_SWAPS = (
    (
        _DM_ONLY,
        "           OR NOT (EXISTS (SELECT 1 FROM campaign_dms d\n"
        "                           WHERE d.guild_id = g AND d.campaign_id = c\n"
        "                             AND d.user_id = dmbot_current_user())\n"
        "                   OR dmbot_web_reads_campaign(g, c)) THEN",
        1,
    ),
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


def _door(name: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + name
        + r"""';"""
        + _steps(swaps)
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = (
    r"""
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
            || replace(d, '])', ', ''maps-add''::text])');
    END
    $do$;

    -- The text limit, read back as it is: a `maps-add` text gets the long limit. The new case goes
    -- before the players-line case, which must be there exactly once (the length check).
    DO $do$
    DECLARE
        d text;
        fresh text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        fresh := replace(d, 'WHEN (action = ''players-line''::text) THEN 120',
                         'WHEN (action = ''maps-add''::text) THEN 1700'
                         || ' WHEN (action = ''players-line''::text) THEN 120');
        IF fresh = d OR (length(fresh) - length(d))
                        <> length(' WHEN (action = ''maps-add''::text) THEN 1700') THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check ' || fresh;
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
    + _door("dmbot_web_name_action_add", _QUEUE_SWAPS)
    + _door("dmbot_web_name_action_read", _READ_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
