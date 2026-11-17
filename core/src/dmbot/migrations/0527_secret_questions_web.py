"""0527_secret_questions_web

The website lists and answers the 🤫 Known or secret? questions (#4598, the web half of #4524 slice
2): a DM-only read door, and three more actions on the queue door, level with Discord's card.
"""

from __future__ import annotations

# `dmbot_web_secret_questions(g, c)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0519's door; else the waiting questions, oldest first, each `{id, kind
# ('narrow' | 'replace' | 'reveal'), label, limits, item, owner, knowers, new}`: the item as the
# card names it (an entry `{table: 'entity', name, type}`, an other name `{table: 'alias', text,
# owner}` or a connection `{table: 'relation', subject, subjectType, predicate, detail, object}`;
# NULL when the item is gone or is no longer a kept connection, which the page words as
# Discord does: a question is only ever asked about a kept one), `limits` (a clue made known to
# some only), `knowers` (the names of the player characters a narrowing would take off), and for a
# replace question `new`, the connection that replaces it. A 🌍 World read's question (its change
# says `common`; only a reveal ever does, `ui/secret_questions.is_world`) is left out: it is
# answered one upload at a time in Discord. The label is the
# DM's own upload or session label; the evidence is never words of a transcript. Read only.
#
# The three actions go on the queue door as 0519's did, filed under no name with no target and no
# flag; the door checks only their shape, the bot applies each through the store call Discord's
# card makes (`answer_secret_question`, `undo`), one change with one Undo, and checks the rest
# again (a question answered meanwhile, the DM):
# - `secret-keep`: `<question id>` (Keep it known, Keep both, or Keep it secret);
# - `secret-make`: `<question id>` (Make it secret, End the old one, or Let them know);
# - `secret-undo`: the change number a make made, checked by the bot against the DM's own done
#   secret actions.
# The queue door's lists are read back from the database as they are and each anchor must be found
# exactly once, so a drift stops the migration. No row is changed.
ACTIONS = ("secret-keep", "secret-make", "secret-undo")
_ID = "[0-9a-f]{32}"

_SHAPES = (
    f"           OR (act IN ('secret-keep', 'secret-make') AND clean !~ '^{_ID}$')\n"
    f"           OR (act = 'secret-undo' AND clean !~ '^[0-9]{{1,15}}$')\n"
)
_LIST = (
    "'part-add', 'part-fix', 'part-state', 'part-show', 'part-remove', 'branch-move',"
    " 'branch-not', 'part-undo', 'part-date', 'lang-add', 'lang-remove', 'lang-check',"
    " 'lang-undo'"
)
_NEW_LIST = _LIST + ", " + ", ".join(f"'{a}'" for a in ACTIONS)

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after 0519's
    (
        "           OR (act = 'lang-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'lang-undo' AND clean !~ '^[0-9]{1,15}$')\n" + _SHAPES,
        1,
    ),
    # no flag
    (
        f"{_LIST})\n               AND COALESCE(fl, false))",
        f"{_NEW_LIST})\n               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (f"{_LIST}) THEN", f"{_NEW_LIST}) THEN", 1),
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

    CREATE FUNCTION dmbot_web_secret_questions(g BIGINT, c TEXT) RETURNS JSONB
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
                'id', q.id, 'kind', q.kind, 'label', q.label,
                'limits', COALESCE((q.change ->> 'audience_limited')::boolean, false),
                'item', CASE q.item_table
                    WHEN 'memory_entities' THEN (
                        SELECT jsonb_build_object('table', 'entity', 'name', e.name,
                                                  'type', e.type)
                        FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.id = q.item_id)
                    WHEN 'memory_aliases' THEN (
                        SELECT jsonb_build_object('table', 'alias', 'text', a.text,
                                                  'owner', o.name)
                        FROM memory_aliases a
                        JOIN memory_entities o ON o.guild_id = a.guild_id
                            AND o.campaign_id = a.campaign_id AND o.id = a.entity_id
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.id = q.item_id)
                    ELSE (
                        SELECT jsonb_build_object('table', 'relation', 'subject', s.name,
                                                  'subjectType', s.type, 'predicate', r.predicate,
                                                  'detail', r.detail, 'object', t.name)
                        FROM memory_relations r
                        JOIN memory_entities s ON s.guild_id = r.guild_id
                            AND s.campaign_id = r.campaign_id AND s.id = r.subject_id
                        JOIN memory_entities t ON t.guild_id = r.guild_id
                            AND t.campaign_id = r.campaign_id AND t.id = r.object_id
                        WHERE r.guild_id = g AND r.campaign_id = c AND r.id = q.item_id
                          AND r.status = 'confirmed') END,
                'knowers', COALESCE((
                    SELECT jsonb_agg(k.name ORDER BY k.name)
                    FROM memory_entities k
                    WHERE k.guild_id = g AND k.campaign_id = c
                      AND jsonb_typeof(q.change -> 'knowers_off') = 'array'
                      AND (q.change -> 'knowers_off') ? k.id), '[]'::jsonb),
                'new', CASE WHEN q.kind = 'replace' AND q.change ? 'by' THEN (
                        SELECT jsonb_build_object('subject', s.name, 'subjectType', s.type,
                                                  'predicate', r.predicate, 'detail', r.detail,
                                                  'object', t.name)
                        FROM memory_relations r
                        JOIN memory_entities s ON s.guild_id = r.guild_id
                            AND s.campaign_id = r.campaign_id AND s.id = r.subject_id
                        JOIN memory_entities t ON t.guild_id = r.guild_id
                            AND t.campaign_id = r.campaign_id AND t.id = r.object_id
                        WHERE r.guild_id = g AND r.campaign_id = c
                          AND r.id = q.change ->> 'by'
                          AND r.status = 'confirmed') END
                ) ORDER BY q.created_at, q.id)
            FROM secret_questions q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.status = 'waiting'
              AND NOT (q.change ? 'common')), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_secret_questions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_secret_questions(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_secret_questions", "dmbot_web_secret_questions(BIGINT, TEXT)"),
)
