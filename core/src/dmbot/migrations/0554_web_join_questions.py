"""0554_web_join_questions

The website answers the DM's join question (#4778 part b, party knowledge slice 2b; Supervisor's
ruling on #4537; behind `DMBOT_PARTY_JOIN` and the page's switch).
"""

from __future__ import annotations

# The DM's answer to "What does Mira know about what came before?" goes on the queue door as the
# World notes ones did: filed under no name, no target, no flag. `join-everyone` ("Only what
# everyone knows": nothing is written) and `join-party` ("What the party knows": one change, one
# Undo) carry the message's group (32 hex); `join-undo` carries the change number the answer gave
# (digits). The bot runs the store's own `join_answer_everyone` / `join_answer_party`, which check
# the question is still waiting (another DM may have answered), and an Undo only of a change this
# DM's own done `join-party` made (read again). The read door, `dmbot_web_join_questions`, lists
# the DM's waiting groups: only questions already sent (`sent_at`, never during play) whose
# character is still a confirmed player character in the party (open stretch), by character
# names only; DM only (NULL for anyone else). The door and the actions do not check
# `DMBOT_PARTY_JOIN`: with it off the bot makes no join questions, so there is nothing to list, and
# the page's own switch keeps the card off until the bot step is deployed. Ids and names, never a
# word of the story. The queue door's lists, shape check and the table's action list are read
# back as they are and each anchor must be found exactly once, so a drift stops the migration. No
# row is changed.
ACTIONS = ("join-everyone", "join-party", "join-undo")
_ID = "[0-9a-f]{32}"
_PREV = "'clue-undo', 'lore-pdf-estimate'"
_NEXT = _PREV + ", 'join-everyone', 'join-party', 'join-undo'"

_LAST_SHAPE = "           OR (act = 'clue-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE + f"           OR (act = 'lore-pdf-estimate' AND clean !~ '^{_ID}$')\n",
        _LAST_SHAPE
        + f"           OR (act = 'lore-pdf-estimate' AND clean !~ '^{_ID}$')\n"
        + f"           OR (act IN ('join-everyone', 'join-party') AND clean !~ '^{_ID}$')\n"
        + "           OR (act = 'join-undo' AND clean !~ '^[0-9]{1,15}$')\n",
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

    CREATE FUNCTION dmbot_web_join_questions(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_agg(jsonb_build_object('group', t.group_id, 'answer', t.answer,
                                                'names', t.names)
                             ORDER BY t.first_at, t.group_id)
            FROM (SELECT q.group_id,
                         CASE WHEN bool_or(q.status = 'waiting') THEN 'waiting'
                              ELSE 'everyone' END AS answer,
                         jsonb_agg(e.name ORDER BY lower(e.name), e.id) AS names,
                         min(q.created_at) AS first_at
                  FROM memory_join_questions q
                  JOIN memory_entities e ON e.guild_id = q.guild_id
                                        AND e.campaign_id = q.campaign_id AND e.id = q.entity_id
                  WHERE q.guild_id = g AND q.campaign_id = c AND q.sent_at IS NOT NULL
                    AND q.status IN ('waiting', 'everyone')
                    AND e.type = 'character' AND e.role = 'player_character'
                    AND e.status = 'confirmed' AND e.merged_into IS NULL
                    AND EXISTS (SELECT 1 FROM memory_party_spans p
                                WHERE p.guild_id = q.guild_id AND p.campaign_id = q.campaign_id
                                  AND p.entity_id = q.entity_id AND p.to_at IS NULL)
                  GROUP BY q.group_id) t), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_join_questions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_join_questions(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_join_questions", "dmbot_web_join_questions(BIGINT, TEXT)"),
)
