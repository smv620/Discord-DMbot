"""0604_web_deeds

The website's "Deeds the party did" (#3591, the core half): the DM answers the deeds DMbot noticed
(`memory_deed_proposals`) from the page exactly as Discord's deed card does. No new table.
"""

from __future__ import annotations

# `dmbot_web_deeds_waiting(g, c, terms, outside)` lists the campaign's deeds waiting for a yes
# (`status = 'proposed'`) in the order `DeedProposals.waiting` gives them (oldest first, at most 50,
# with the total), as `{id, doer, text, viewers, effect, kind}`: the model's own short telling,
# so DM only (NULL for anyone who is not a DM of the campaign, a player included). A deed is
# listed and counted only while every one of its speakers is recorded now, by the bot's rule
# (`consent._select`, as 0341 does for claims): a consent row under the current wording (`terms`)
# and, while an outside company hears the voices, for that company (`outside`). Both arrive as
# arguments because the database cannot know them. A pause (off the record for the moment) lives
# in the bot's memory and a door cannot see it, the gap Supervisor accepted on #3464; the bot asks
# again when the DM answers. The door never returns the line numbers, the speakers or the session
# a deed came from. Reading changes nothing (the bot's own list also deletes the deeds of someone
# who stopped; the page only leaves them out, and the bot deletes them at its next read).
#
# Three actions go on the queue door, each filed under no name, no target and no flag; the door
# checks only the shapes and the bot checks the rest again. `deed-record` and `deed-not` carry the
# deed's id (32 hex); `deed-undo` carries the change number a recorded answer gave (1 to 15 digits).
# The queue door's lists, shape check and the table's action list are read back as they are and
# each anchor must be found exactly the number of times given.
ACTIONS = ("deed-record", "deed-not", "deed-undo")
_ID = "[0-9a-f]{32}"
_QUEUE_SWAPS = (
    # the allowed actions, the actions without a flag, and the ones filed under no name
    (
        "'road-condition-show', 'route-length'",
        "'road-condition-show', 'deed-record', 'deed-not', 'deed-undo', 'route-length'",
        3,
    ),
    # the text's shapes, before the Undo's of World notes
    (
        "           OR (act = 'world-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        f"           OR (act = 'deed-record' AND clean !~ '^{_ID}$')\n"
        f"           OR (act = 'deed-not' AND clean !~ '^{_ID}$')\n"
        "           OR (act = 'deed-undo' AND clean !~ '^[0-9]{1,15}$')\n"
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
    -- The allowed actions, read back from the table as it is and three more added at the end.
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

    CREATE FUNCTION dmbot_web_deeds_waiting(g BIGINT, c TEXT, terms INTEGER, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        total BIGINT;
        listed JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT count(*) INTO total FROM memory_deed_proposals p
            WHERE p.guild_id = g AND p.campaign_id = c AND p.status = 'proposed'
              AND p.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n
                                         WHERE n.guild_id = g AND n.terms_version = terms
                                           AND (outside IS NULL OR n.outside_to = outside));
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'id', x.id, 'doer', x.doer, 'text', x.text, 'viewers', to_jsonb(x.viewers),
                   'effect', x.effect, 'kind', x.kind) ORDER BY x.created_at, x.id), '[]'::jsonb)
            INTO listed
            FROM (
                SELECT p.id, p.doer, p.text, p.viewers, p.effect, p.kind, p.created_at
                FROM memory_deed_proposals p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.status = 'proposed'
                  AND p.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n
                                             WHERE n.guild_id = g AND n.terms_version = terms
                                               AND (outside IS NULL OR n.outside_to = outside))
                ORDER BY p.created_at, p.id LIMIT 50) x;
        RETURN jsonb_build_object('total', total, 'deeds', listed);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_deeds_waiting(BIGINT, TEXT, INTEGER, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_deeds_waiting(BIGINT, TEXT, INTEGER, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_deeds_waiting", "dmbot_web_deeds_waiting(BIGINT, TEXT, INTEGER, TEXT)"),
)
