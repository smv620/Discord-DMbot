"""0563_web_heard_waiting

The website's "Did they mean…?" (#4841 part B, slice B1; Supervisor's ruling on #4841): the DM's
open questions and the pick of a name or "Keep as heard".
"""

from __future__ import annotations

# `dmbot_web_heard_waiting(g, c)` reads the campaign's open `waiting_name_questions` as the bot's
# `WaitingQuestionStore.waiting` does: open, younger than the seven days after which one expires
# quietly, and only while the line it is about still exists (the line is not copied here: only its
# existence is checked, and only the heard word is returned, as Discord shows it to the DM). Each
# question gives its id, the heard word, who said it (the player's confirmed character names, else
# null: the page says "a player") and the candidate entries from `option_ids` in their own order,
# each with the index Discord's buttons carry. A candidate is listed only if it is confirmed, not
# merged, not hidden from the players, not for some only, not an unnamed person and has no secret
# other name at all: stricter than the bot's `own_name`, never looser, and the bot checks again
# when the answer arrives (`name-gone`). It must also have a confirmed, not secret name entry
# spelled as the entity's own name (what `own_name` looks for), or the page would offer a name
# the bot then refuses. DM only (NULL for anyone else). At most 50 questions, with
# the total. The queue door's lists, shape check and the table's action list are read back as they
# are and each anchor must be found exactly once. `heard-pick` carries `<question id> <index>` and
# `heard-keep` the question id; the bot answers with `DMBot.answer_waiting_coded` (the same
# function as Discord) and the answer carries its code, never a word. No table changes.
ACTIONS = ("heard-pick", "heard-keep")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo'"
)
_NEXT = _PREV + ", 'heard-pick', 'heard-keep'"

_LAST_SHAPE = "           OR (act = 'rules-update-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + "           OR (act = 'heard-pick' AND clean !~ '^[0-9]{1,15} [0-9]$')\n"
        + "           OR (act = 'heard-keep' AND clean !~ '^[0-9]{1,15}$')\n",
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
    CREATE FUNCTION dmbot_web_heard_waiting(g BIGINT, c TEXT) RETURNS JSONB
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
        SELECT count(*) INTO total FROM waiting_name_questions q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.state = 'open'
              AND q.created_at >= extract(epoch FROM now())::BIGINT - 604800
              AND EXISTS (SELECT 1 FROM transcript_lines l
                          WHERE l.guild_id = g AND l.session_id = q.session_id
                            AND l.user_id = q.speaker_id AND l.started_ms = q.started_ms
                            AND l.sidebar IS NULL AND NOT l.typed);
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'id', x.id, 'heard', x.heard, 'speaker', x.speaker,
                   'options', x.options) ORDER BY x.created_at, x.id), '[]'::jsonb)
            INTO listed
            FROM (
                SELECT q.id, q.heard, q.created_at,
                       (SELECT string_agg(pc.name, ' / ' ORDER BY pc.created_at, pc.id)
                        FROM memory_entities pc
                        WHERE pc.guild_id = g AND pc.campaign_id = c
                          AND pc.type = 'character' AND pc.role = 'player_character'
                          AND pc.status = 'confirmed' AND pc.merged_into IS NULL
                          AND pc.played_by = q.speaker_id) AS speaker,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                                      'index', o.n - 1, 'id', e.id, 'name', e.name)
                                      ORDER BY o.n)
                           FROM unnest(q.option_ids) WITH ORDINALITY AS o(id, n)
                           JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c
                                AND e.id = o.id
                           WHERE e.status = 'confirmed' AND e.merged_into IS NULL
                             AND NOT e.hidden_from_players AND NOT e.unnamed
                             AND NOT e.audience_limited
                             AND NOT EXISTS (SELECT 1 FROM memory_aliases a
                                             WHERE a.guild_id = g AND a.campaign_id = c
                                               AND a.entity_id = e.id AND a.secret
                                               AND a.status <> 'rejected')
                             AND EXISTS (SELECT 1 FROM memory_aliases m
                                         WHERE m.guild_id = g AND m.campaign_id = c
                                           AND m.entity_id = e.id AND m.text = e.name
                                           AND m.status = 'confirmed' AND NOT m.secret)
                       ), '[]'::jsonb) AS options
                FROM waiting_name_questions q
                WHERE q.guild_id = g AND q.campaign_id = c AND q.state = 'open'
                  AND q.created_at >= extract(epoch FROM now())::BIGINT - 604800
                  AND EXISTS (SELECT 1 FROM transcript_lines l
                              WHERE l.guild_id = g AND l.session_id = q.session_id
                                AND l.user_id = q.speaker_id AND l.started_ms = q.started_ms
                                AND l.sidebar IS NULL AND NOT l.typed)
                ORDER BY q.created_at, q.id LIMIT 50) x;
        RETURN jsonb_build_object('total', total, 'questions', listed);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_heard_waiting(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_heard_waiting(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_heard_waiting", "dmbot_web_heard_waiting(BIGINT, TEXT)"),
)
