"""0528_thread_progress_web

The website lists and answers the "Progress to check" lines on story threads (#4616, the web half
of #4522 quests slice 5b): a DM-only read door, and three more actions on the queue door, level
with Discord's 🧵 Progress to check card.
"""

from __future__ import annotations

# `dmbot_web_thread_progress(g, c, terms, outside)`: NULL for anyone who is not a DM of the
# campaign (or a server the caller is not in), as 0519's door; else the waiting lines of progress
# on threads the campaign keeps, oldest first, each `{id, threadId, thread, threadType, kind
# ('part' | 'part_state' | 'thread_state' | 'clue'), partKind, words, state, due, partWords (the
# part a 'part_state' line moves), clue (a found clue the campaign has, by name), clueKnown,
# finders (their names, in order), session, sessionAt}`. The page words each line as Discord's
# card does (`web/progress_line_words.py`, held equal to `ui/thread_progress_card.line_text` by a
# test). A line is listed only while every speaker it quotes is recorded now, by the rule 0518 gave
# the thread suggestions (`consent.still_recorded`: a consent row under the current wording and,
# while an outside company hears the voices, for that company, an import confirmation not taken
# back, or a speaker without Discord); `terms` and `outside` arrive as arguments because the
# database cannot know them. No transcript word is here: a line keeps only the session (by its
# date), line starts and speakers, and none of those leave. Read only.
#
# The three actions go on the queue door as 0519's did, filed under no name with no target and no
# flag; the door checks only their shape, the bot applies each through the store call Discord's
# card makes (`keep_thread_progress`, `turn_down`, `unturn`, `undo`) and checks the rest again (the
# lines still wait, all of one thread, the DM):
# - `progress-keep`: `<line ids left on, or -> <line ids left off, or ->` (ids joined by commas,
#   25 at most between the two; the left-off ones are turned down in the same change);
# - `progress-none`: `<line ids>` (🚫 None happened);
# - `progress-undo`: `keep <the change number a keep made>` or `none <the line ids turned down>`.
# The three texts may be longer than a hundred characters, so their text limit is the long one
# `part-add` has. The queue door's lists, shape check and text limit are read back from the
# database as they are and each anchor must be found exactly once, so a drift stops the migration.
# No row is changed.
ACTIONS = ("progress-keep", "progress-none", "progress-undo")
_ID = "[0-9a-f]{32}"
_IDS = f"{_ID}(,{_ID}){{0,24}}"

_SHAPES = (
    f"           OR (act = 'progress-keep' AND clean !~ '^(-|{_IDS}) (-|{_IDS})$')\n"
    f"           OR (act = 'progress-none' AND clean !~ '^{_IDS}$')\n"
    f"           OR (act = 'progress-undo' AND clean !~ '^(keep [0-9]{{1,15}}|none {_IDS})$')\n"
)
_LIST = (
    "'part-add', 'part-fix', 'part-state', 'part-show', 'part-remove', 'branch-move',"
    " 'branch-not', 'part-undo', 'part-date', 'lang-add', 'lang-remove', 'lang-check',"
    " 'lang-undo', 'secret-keep', 'secret-make', 'secret-undo'"
)
_NEW_LIST = _LIST + ", " + ", ".join(f"'{a}'" for a in ACTIONS)

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after 0527's
    (
        "           OR (act = 'secret-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        "           OR (act = 'secret-undo' AND clean !~ '^[0-9]{1,15}$')\n" + _SHAPES,
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
    # the long text limit list in the door (the function's own check, beside the table's)
    (
        "'maps-add', 'thread-keep', 'part-add', 'part-fix')",
        "'maps-add', 'thread-keep', 'part-add', 'part-fix', 'progress-keep', 'progress-none',"
        " 'progress-undo')",
        1,
    ),
)

_RECORDED = (
    "NOT EXISTS (SELECT 1 FROM unnest(s.speaker_ids) AS u(id) WHERE u.id > 0 AND NOT ("
    "EXISTS (SELECT 1 FROM consent n WHERE n.guild_id = g AND n.user_id = u.id "
    "AND n.terms_version = terms AND (outside IS NULL OR n.outside_to = outside)) "
    "OR (EXISTS (SELECT 1 FROM import_consents i WHERE i.guild_id = g AND i.campaign_id = c "
    "AND i.user_id = u.id AND i.removed_at IS NULL) "
    "AND NOT EXISTS (SELECT 1 FROM consent_refusals r "
    "WHERE r.guild_id = g AND r.user_id = u.id))))"
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


    -- The text limit, read back as it is: the three texts get the long limit `part-add` has.
    DO $do$
    DECLARE
        d text;
        fresh text;
        one text := ' WHEN (action = ''part-add''::text) THEN 1700';
        more text := ' WHEN (action = ''progress-keep''::text) THEN 1700'
            || ' WHEN (action = ''progress-none''::text) THEN 1700'
            || ' WHEN (action = ''progress-undo''::text) THEN 1700';
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, one, ''))) <> length(one) THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        fresh := replace(d, one, more || one);
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

    CREATE FUNCTION dmbot_web_thread_progress(g BIGINT, c TEXT, terms INTEGER, outside TEXT)
    RETURNS JSONB
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
                'id', s.id, 'threadId', s.thread_id, 'thread', t.name, 'threadType', t.type,
                'kind', s.kind, 'partKind', s.part_kind, 'words', s.words, 'state', s.state,
                'due', s.due,
                'partWords', (SELECT p.words FROM memory_thread_parts p
                              WHERE p.guild_id = g AND p.campaign_id = c AND p.id = s.part_id),
                'clueKnown', s.clue_id IS NOT NULL,
                'clue', (SELECT e.name FROM memory_entities e
                         WHERE e.guild_id = g AND e.campaign_id = c AND e.id = s.clue_id),
                'finders', COALESCE((
                    SELECT jsonb_agg(e.name ORDER BY f.o)
                    FROM unnest(s.finder_ids) WITH ORDINALITY AS f(id, o)
                    JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c
                                          AND e.id = f.id), '[]'::jsonb),
                'session', s.session_id, 'sessionAt', s.session_at)
                ORDER BY s.created_at, s.id)
            FROM thread_progress s
            JOIN memory_entities t ON t.guild_id = s.guild_id AND t.campaign_id = s.campaign_id
                                  AND t.id = s.thread_id
            WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'waiting'
              AND """
    + _RECORDED
    + r"""), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_thread_progress(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_thread_progress(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_thread_progress", "dmbot_web_thread_progress(BIGINT, TEXT, INT, TEXT)"),
)
