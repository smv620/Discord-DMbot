"""0471_assumed_link_doors

A place's assumed parent on the website (#4304, #4173 slice 3): the DM's Full View carries each
connection's `assumed` mark, and a new queued action, `link-right`, is the DM's ✅ That's right.
"""

from __future__ import annotations

# `memory_relations.assumed` is 0462's. The relationship door (`dmbot_web_relationships`, which
# also serves a peeking player) now sends it on each connection, so the page can say "(assumed)"
# and offer the button. Only the DM is ever told: `web/relationships.edges_from` puts it in the
# answer for a DM alone, and an assumed row is held back, so the players' views never carry the
# row at all (`test_web_assumed_links.py`).
#
# `link-right` names one connection as its target, like the other link edits, and carries no text
# and no flag; the bot applies it with `MemoryStore.confirm_assumed`. The action list and the queue
# door are read back as they are and each anchor must be found the exact number of times, so a
# drift stops the migration instead of changing the wrong line (0435's way). No row is changed.
_DOOR_SWAPS = (
    # the actions the door takes, and the ones that name a connection as their target
    (
        "'link-pin', 'link-undo', 'link-direction',",
        "'link-pin', 'link-undo', 'link-direction', 'link-right',",
        3,
    ),
    # its text is empty, and it carries no flag
    (
        "           OR (act = 'link-direction'\n",
        "           OR (act = 'link-right' AND (clean <> '' OR COALESCE(fl, false)))\n"
        "           OR (act = 'link-direction'\n",
        1,
    ),
)
_RELATIONSHIPS_SWAPS = (
    ("'route_state', r.route_state", "'route_state', r.route_state, 'assumed', r.assumed", 1),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made, and made again (its settings
    come back with `pg_get_functiondef`, and CREATE OR REPLACE keeps its grants)."""
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
    -- The allowed actions, read back from the table as it is and one more added at the end, so an
    -- action another migration added stays in the list whatever order the migrations merge in.
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
            || replace(d, '])', ', ''link-right''::text])');
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
    + _swap("dmbot_web_name_action_add", _DOOR_SWAPS)
    + _swap("dmbot_web_relationships", _RELATIONSHIPS_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
