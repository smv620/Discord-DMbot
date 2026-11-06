"""0241_names_fixed_undo

And undoes a fix, queued for the bot (#2684).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _READING_LIST,
    READING_ACTIONS,
    WEB_WAITING_ACTIONS_LIST,
    _reading_door__2,
)

# The recap page's Undo of a name-pass fix (#2684): one more action on the website's queue, for the
# bot to apply through the very calls the Discord Undo makes. `target_id` is the fix's Undo number
# (`name_pass_fixes.batch`, as text). The door says no unless that fix is the campaign's own; one
# already undone is queued and answered `gone` by the bot (nothing is changed).
NAMES_FIXED_ACTIONS = ("names-fixed-undo",)

_NAMES_FIXED_LIST = ", ".join(f"'{a}'" for a in NAMES_FIXED_ACTIONS)

_names_fixed_door = _reading_door__2

_all_readings = ", ".join(f"'{a}'" for a in READING_ACTIONS)

if _names_fixed_door.count(_all_readings) != 5:
    raise RuntimeError("the name-action door no longer lists the reading actions five times")

_names_fixed_door = _names_fixed_door.replace(
    _all_readings, f"{_all_readings}, {_NAMES_FIXED_LIST}"
)

for _old, _new in (
    (
        # (a fix is named by its Undo number, a decimal; every other target is a 32-hex id)
        "AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))\n           OR (act NOT IN (",
        "AND (tgt IS NULL OR tgt !~ CASE WHEN act = 'names-fixed-undo' THEN '^[0-9]{1,12}$'\n"
        "                                             ELSE '^[0-9a-f]{32}$' END))\n"
        "           OR (act NOT IN (",
    ),
    (
        "act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%' THEN",
        "act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%'\n"
        "           OR act LIKE 'names-fixed-%' THEN",
    ),
    (
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
        "            ELSIF act = 'names-fixed-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_pass_fixes f\n"
        "                   WHERE f.guild_id = g AND f.campaign_id = c\n"
        "                   AND f.batch::text = tgt) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (",
    ),
):
    if _names_fixed_door.count(_old) != 1:  # (an explicit raise: `python -O` drops an assert)
        raise RuntimeError(
            f"the name-action door changed under the names-fixed migration: {_old!r}"
        )
    _names_fixed_door = _names_fixed_door.replace(_old, _new)

NAMES_FIXED_UNDO = (
    f"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}, {_READING_LIST}, {_NAMES_FIXED_LIST}));
    """
    + _names_fixed_door
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    """
)

SQL = NAMES_FIXED_UNDO
