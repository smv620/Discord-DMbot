"""0569_web_join_choices

The website's "Let me choose" for the join question (party knowledge slice 4c, web half; #4799;
Supervisor's ruling on #4799): the choices the bot worked out for the DM, and the pick.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# "Let me choose" is the graph's own logic (`MemoryStore.join_choices`: the sessions and story
# threads with something to give each character, and how much), so it is Python, not a SQL door, and
# its answer is too big for a queue answer (200 characters). `join-choices` carries the question's
# group (32 hex); the bot works the choices out and keeps them as one row of `web_join_choices` for
# that DM: ids, counts and the story threads' names (what the DM reads on the Names page), no
# transcript or card word. A new read replaces the row. The DM-only door `dmbot_web_join_choices`
# gives the row back, only to the DM it was made for (a co-DM gets nothing), with the characters'
# names. `join-choose` carries `<group> s<session start>` or `<group> t<thread id>`; the bot gives
# the pick with `MemoryStore.join_choose`, which checks everything again (the question still open
# for the character, the pick still in the party's gap), so a stale page can never give more than
# the bot would at that moment. The table is swept after an hour by the bot, is in the forget
# catalog by `user_id`, goes with the campaign (the foreign key is ON DELETE CASCADE and, as every
# `guild_id` key, ON UPDATE CASCADE), has forced row-level security and is not in a backup (it is
# worked out again from the graph). The queue door's lists, shape check and the table's action list
# are read back as they are and each anchor must be found exactly once.
ACTIONS = ("join-choices", "join-choose")
_PREV = (
    "'join-undo', 'party-leave', 'party-back', 'party-undo', 'party-catch-up-choices', "
    "'party-catch-up', 'rules-update-pick', 'rules-update-undo', 'heard-pick', 'heard-keep', "
    "'heard-type', 'rules-update-undo-last', 'pack-update-undo-last', 'pack-update-pick', "
    "'pack-update-undo'"
)
_NEXT = _PREV + ", 'join-choices', 'join-choose'"

_ID = "[0-9a-f]{32}"
_LAST_SHAPE = "           OR (act = 'pack-update-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE,
        _LAST_SHAPE
        + f"           OR (act = 'join-choices' AND clean !~ '^{_ID}$')\n"
        + f"           OR (act = 'join-choose' AND clean !~ '^{_ID} (s[0-9]{{1,15}}|t{_ID})$')\n",
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
    CREATE TABLE web_join_choices (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        group_id    TEXT NOT NULL CHECK (group_id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        payload     JSONB NOT NULL,
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, group_id, user_id)
    );
    CREATE INDEX web_join_choices_age ON web_join_choices (guild_id, created_at);
    """
    + _isolate("web_join_choices")
    + r"""
    CREATE FUNCTION dmbot_web_join_choices(g BIGINT, c TEXT, grp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_join_choices%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM web_join_choices w
            WHERE w.guild_id = g AND w.campaign_id = c AND w.group_id = grp
              AND w.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN jsonb_build_object('choices', NULL);
        END IF;
        RETURN jsonb_build_object(
            'madeAt', r.created_at,
            'choices', r.payload || jsonb_build_object('names', COALESCE((
                SELECT jsonb_object_agg(e.id, e.name)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c
                  AND e.id IN (SELECT jsonb_array_elements_text(r.payload -> 'members'))
            ), '{}'::jsonb)));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_join_choices(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_join_choices(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("web_join_choices",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_join_choices", "dmbot_web_join_choices(BIGINT, TEXT, TEXT)"),
)
