"""0585_web_world_biases

The website lists, in World notes, the feelings DMbot read from the lore ("Mithral Keep is
unfriendly toward elves") and the DM answers them as in Discord's Feelings group (#4932, the web
half of #4229 slice 4).
"""

from __future__ import annotations

# `dmbot_web_world_biases(g, c)`: NULL unless the caller is a DM of the campaign (0404's check).
# Else the DM's open reads of a feeling (`memory_attitudes` with `status = 'read'`, not closed, the
# rows `biases.waiting` reads), oldest first, `{id, holderId, holder, lineage, culture, score}`: the
# holder an entry the DM keeps (confirmed or waiting, not merged: the list's own rule), and the
# target a lineage DMbot knows or a named culture that is not turned down. The page words the line
# with `memory/biases.py` and `standing.TOWARD` (the step from the score, a lineage's plural), so
# the page and Discord never differ. Names, ids and numbers only: no line of lore.
#
# The queue door gets one new action, `world-bias` (✏️ Change: `bias:<row id> <step>`, a step of
# `standing.STEPS`); the bot runs `MemoryStore.change_read_bias` (kept at that step as the DM's
# own, one change with the World notes Undo). `world-keep` and `world-not` take a `bias:` line
# besides the others (the bot's `world_notes.keep` and `turn_down` already handle it). The door's
# lists and shape checks are read back from the database as they are, and each anchor must be
# found exactly the number of times given. No row changes.
ACTIONS = ("world-bias",)
_ID = "[0-9a-f]{32}"
_STEPS_RE = "Hostile|Unfriendly|Wary|Friendly|Devoted"
_LIST = "'world-keep', 'world-not', 'world-undo', 'maps-use'"

_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (_LIST, "'world-keep', 'world-not', 'world-undo', 'world-bias', 'maps-use'", 3),
    # ✅ Keep takes a bias line
    (
        f"act = 'world-keep' AND clean !~ '^(place|language|road):{_ID}"
        f"( (place|language|road):{_ID}){{0,24}}$'",
        f"act = 'world-keep' AND clean !~ '^(place|language|road|bias):{_ID}"
        f"( (place|language|road|bias):{_ID}){{0,24}}$'",
        1,
    ),
    # 🚫 Not right takes one
    (
        f"act = 'world-not' AND clean !~ '^(place|language):{_ID}$'",
        f"act = 'world-not' AND clean !~ '^(place|language|bias):{_ID}$'",
        1,
    ),
    # the new shape, before the Undo's
    (
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        f"           OR (act = 'world-bias' AND clean !~ '^bias:{_ID} ({_STEPS_RE})$')\n"
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
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

    CREATE FUNCTION dmbot_web_world_biases(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', a.id, 'holderId', e.id, 'holder', e.name,
                       'lineage', COALESCE(a.toward_lineage, ''), 'culture', COALESCE(cu.name, ''),
                       'score', a.score) ORDER BY a.created_at, a.id)
            FROM memory_attitudes a
            JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                                  AND e.id = a.holder_id
                                  AND e.status IN ('confirmed', 'proposed')
                                  AND e.merged_into IS NULL
            LEFT JOIN memory_cultures cu ON cu.guild_id = a.guild_id
                                        AND cu.campaign_id = a.campaign_id
                                        AND cu.id = a.toward_culture_id
                                        AND cu.status <> 'rejected'
            WHERE a.guild_id = g AND a.campaign_id = c AND a.status = 'read'
              AND a.closed_at IS NULL
              AND (a.toward_lineage IS NOT NULL OR cu.id IS NOT NULL)), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_world_biases(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_world_biases(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_world_biases", "dmbot_web_world_biases(BIGINT, TEXT)"),
)
