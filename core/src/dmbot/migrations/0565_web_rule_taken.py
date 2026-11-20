"""0565_web_rule_taken

The website's Undo of an update DMbot already took on a followed house-rules set (#4841 part C,
slice C3; Supervisor's ruling on #4841): a DM-only read door for the take and one queue action.
"""

from __future__ import annotations

# The Undo of an update DMbot already took (#4841 part C, slice C3; Supervisor's ruling on #4841):
# at a session's start DMbot takes a followed house-rules set's newer version (as Discord says), and
# Discord's note has an Undo that puts the copy before it back (`RuleSetStore.undo_last_take`).
# That copy is kept in the follow's own row (`previous_version`, `previous_content`), so no table
# changes. `dmbot_web_rule_taken(g, c)` returns it for a DM only (NULL for anyone else): the set's
# name and publisher, the version before and the version now, and the two copies (rules and
# relationship definitions only: what crosses campaigns by design, never a story word). The web
# module works the changes out with `rules.set_diff`, as for a waiting update. The queue action
# `rules-update-undo-last` carries the version the page showed; the bot undoes only while the
# campaign is still on it. The queue door's lists, shape check and the table's action list are read
# back as they are and each anchor must be found exactly once.
ACTIONS = ("rules-update-undo-last",)
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type'"
)
_NEXT = _PREV + ", 'rules-update-undo-last'"

_LAST_SHAPE = "           OR (act = 'heard-type' AND clean !~ '^[0-9]{1,15}( .{1,84})?$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'rules-update-undo-last' AND clean !~ '^[0-9]{1,9}$')\n",
        1,
    ),
    (
        f"{_PREV})\n               AND COALESCE(fl, false))",
        f"{_NEXT})\n               AND COALESCE(fl, false))",
        1,
    ),
    (f"{_PREV}) THEN", f"{_NEXT}) THEN", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)

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

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
    + r"""
    CREATE FUNCTION dmbot_web_rule_taken(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        f rule_set_follows%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO f FROM rule_set_follows r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'house_rules'
              AND r.previous_version IS NOT NULL AND r.previous_content IS NOT NULL
            ORDER BY r.position, r.followed_at, r.set_id LIMIT 1;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('taken', NULL);
        END IF;
        RETURN jsonb_build_object('taken', jsonb_build_object(
            'name', f.set_name, 'publisher', f.publisher, 'from', f.previous_version,
            'to', f.version, 'before', f.previous_content, 'after', f.content));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_taken(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_taken(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rule_taken", "dmbot_web_rule_taken(BIGINT, TEXT)"),
)
