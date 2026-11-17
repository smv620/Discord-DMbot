"""0535_claim_some_know

The website answers a waiting claim with 👥 Some know (#4680, the web half of #4534): the party's
player characters for the picker, and one more action on the queue door, level with Discord's card.
"""

from __future__ import annotations

# `dmbot_web_party_characters(g, c)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0519's door; else `[{id, name}]`, the campaign's confirmed player
# characters (`type = 'character'`, `role = 'player_character'`), A to Z: the picker's list, the
# same people Discord's picker offers. Read only; the ids are the entries' own, which the answer
# needs.
#
# `claim-some` goes on the queue door as 0343's `claim-pick` did: its target is the waiting claim
# (the door's own check that every `claim-` action needs, so the claim must still wait in this
# campaign) and its text is the characters who know it, ids joined by commas (1 to 25). The bot
# applies it through the call Discord's picker makes, `ClaimStore.confirm(knowers=…)`: the
# connection happened and is held back from the players, exactly those characters know it, one
# change with one Undo; it checks the rest again (the characters, the DM, who is recorded). An Undo
# of it is the existing `claim-undo`, which now also accepts a done `claim-some`. A long text, so
# `claim-some` gets the long text limit, in the table's check and in the door's own list. The queue
# door's lists, shape check and text limit are read back from the database as they are and each
# anchor must be found exactly the number of times given, so a drift stops the migration. No row is
# changed.
ACTIONS = ("claim-some",)
_ID = "[0-9a-f]{32}"
_IDS = f"{_ID}(,{_ID}){{0,24}}"

_QUEUE_SWAPS = (
    # the allowed actions
    (
        "'claim-undo', 'backstory-keep', 'backstory-keep-show', 'backstory-skip',"
        " 'backstory-keep-all',",
        "'claim-undo', 'claim-some', 'backstory-keep', 'backstory-keep-show', 'backstory-skip',"
        " 'backstory-keep-all',",
        1,
    ),
    # both target lists (a claim id)
    (
        "'claim-unpick', 'claim-undo', 'backstory-keep', 'backstory-keep-show', 'backstory-skip',\n"
        "                       'backstory-which-pick'",
        "'claim-unpick', 'claim-undo', 'claim-some', 'backstory-keep', 'backstory-keep-show',"
        " 'backstory-skip',\n                       'backstory-which-pick'",
        2,
    ),
    # the text's shape
    (
        "           OR (act = 'claim-undo' AND clean !~ '^[0-9]{1,12}$')\n",
        "           OR (act = 'claim-undo' AND clean !~ '^[0-9]{1,12}$')\n"
        f"           OR (act = 'claim-some' AND clean !~ '^{_IDS}$')\n",
        1,
    ),
    # no flag
    (
        "'claim-pick', 'claim-unpick', 'claim-undo', 'link-undo', 'name',",
        "'claim-pick', 'claim-unpick', 'claim-undo', 'claim-some', 'link-undo', 'name',",
        1,
    ),
    # the Undo of a press takes a done `claim-some` too
    (
        "AND p.action = 'claim-confirm' AND p.state = 'done'",
        "AND p.action IN ('claim-confirm', 'claim-some') AND p.state = 'done'",
        1,
    ),
    # the long text limit list in the door (the function's own check, beside the table's)
    (
        "'part-add', 'part-fix', 'progress-keep', 'progress-none', 'progress-undo', 'clue-add')",
        "'part-add', 'part-fix', 'progress-keep', 'progress-none', 'progress-undo', 'clue-add',"
        " 'claim-some')",
        1,
    ),
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

    -- The text limit, read back as it is: a `claim-some` text gets the long limit `part-add` has.
    DO $do$
    DECLARE
        d text;
        fresh text;
        one text := ' WHEN (action = ''part-add''::text) THEN 1700';
        more text := ' WHEN (action = ''claim-some''::text) THEN 1700';
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

    CREATE FUNCTION dmbot_web_party_characters(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name)
                             ORDER BY lower(e.name), e.id)
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'character'
              AND e.role = 'player_character' AND e.status = 'confirmed'), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_party_characters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_party_characters(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_party_characters", "dmbot_web_party_characters(BIGINT, TEXT)"),
)
