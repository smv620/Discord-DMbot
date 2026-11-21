"""0572_web_leave_questions

The website's "Still in the party?" (party knowledge slice 6a, web half; #4895; Supervisor's ruling
on slice 6): the waiting death and goodbye questions and the two answers.
"""

from __future__ import annotations

# `dmbot_web_leave_questions(g, c)` lists the DM's waiting "Still in the party?" questions, which
# ride the join question's rails (0570: `kind` `leave` or `died` on `memory_join_questions`, one
# message each kind): only questions already sent (`sent_at`, never during play) for a confirmed
# player character who is in the party now (an open stretch), grouped by message and kind, by
# character names and ids only; DM only (NULL for anyone else). The join door keeps to join and
# back (0570), so a death never shows with the join buttons. `leave-yes` ("Left the party") and
# `leave-no` ("Still in the party") carry the character's id (32 hex); the bot runs the store's own
# `staying_answer_left` (the card's call, one change with an Undo that asks again) and
# `staying_answer_stays` (nothing written but the answer), and `join-undo` takes back a
# `leave-yes` change of this DM's own. "Later" is the page's own: it writes nothing. The queue
# door's lists, shape check and the table's action list are read back as they are and each anchor
# must be found exactly once. No table changes. This door is the only place a `leave` or `died`
# question shows on the website (the join door keeps to join and back, 0570): a change to either
# door must look at both.
ACTIONS = ("leave-yes", "leave-no")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo', 'join-choices', 'join-choose'"
)
_NEXT = _PREV + ", 'leave-yes', 'leave-no'"

_ID = "[0-9a-f]{32}"
_LAST_SHAPE = (
    "           OR (act = 'join-choose' AND clean !~ '^" + _ID + " (s[0-9]{1,15}|t" + _ID + ")$')\n"
)
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE + f"           OR (act IN ('leave-yes', 'leave-no') AND clean !~ '^{_ID}$')\n",
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
    CREATE FUNCTION dmbot_web_leave_questions(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_agg(jsonb_build_object('group', t.group_id, 'kind', t.kind,
                                                'characters', t.characters)
                             ORDER BY t.first_at, t.group_id, t.kind)
            FROM (SELECT q.group_id, q.kind,
                         jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name)
                                   ORDER BY lower(e.name), e.id) AS characters,
                         min(q.created_at) AS first_at
                  FROM memory_join_questions q
                  JOIN memory_entities e ON e.guild_id = q.guild_id
                                        AND e.campaign_id = q.campaign_id AND e.id = q.entity_id
                  WHERE q.guild_id = g AND q.campaign_id = c AND q.sent_at IS NOT NULL
                    AND q.group_id IS NOT NULL AND q.status = 'waiting'
                    AND q.kind IN ('leave', 'died')
                    AND e.type = 'character' AND e.role = 'player_character'
                    AND e.status = 'confirmed' AND e.merged_into IS NULL
                    AND EXISTS (SELECT 1 FROM memory_party_spans p
                                WHERE p.guild_id = q.guild_id AND p.campaign_id = q.campaign_id
                                  AND p.entity_id = q.entity_id AND p.to_at IS NULL)
                  GROUP BY q.group_id, q.kind) t), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_leave_questions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_leave_questions(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_leave_questions", "dmbot_web_leave_questions(BIGINT, TEXT)"),
)
