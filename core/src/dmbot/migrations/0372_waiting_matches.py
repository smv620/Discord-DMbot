"""0372_waiting_matches

The website's half of DMbot's own matches (#3602): a DM-only read of the matches DMbot made itself
that the DM has not yet confirmed or undone, and the two actions that answer one.
"""

from __future__ import annotations

# `match-confirm` and `match-undo` (target: the change batch of DMbot's match, in digits, as an Undo
# number) are queued on the same table as every other name action and applied by the bot as the DM
# (`confirm_join` and `undo`). They file under no name (like `confirm-kinds`), carry no words and no
# flag, and the door only lets a DM queue one for a batch that a `who` change really made in this
# campaign; the bot checks again, and re-reads that the match is still waiting, when it applies it.
# The door is read back from the database as it is (many migrations have changed it) and each
# anchor must be found the exact number of times, so a drift stops the migration instead of
# widening the wrong rule, as 0369 does. No row is changed.
_SWAPS = (
    # the allowed actions, the lists that must have no words, the lists that need a target and
    # the lists that must have no flag: `names-fixed-undo` (an Undo number and nothing else) is in
    # all five, so the two new words follow it in each
    (
        "'reading-undo', 'names-fixed-undo',",
        "'reading-undo', 'names-fixed-undo', 'match-confirm', 'match-undo',",
        5,
    ),
    # the target's shape: digits, as an Undo number
    (
        "WHEN act = 'names-fixed-undo' THEN '^[0-9]{1,12}$'",
        "WHEN act IN ('names-fixed-undo', 'match-confirm', 'match-undo') THEN '^[0-9]{1,12}$'",
        1,
    ),
    # filed under no name, like a waiting connection
    (
        "OR act LIKE 'names-fixed-%' OR act = 'name-undo'",
        "OR act LIKE 'names-fixed-%' OR act = 'name-undo'"
        " OR act IN ('match-confirm', 'match-undo')",
        1,
    ),
    # the batch must be one DMbot made by itself in this campaign
    (
        "ELSIF act = 'names-fixed-undo' AND NOT EXISTS (",
        "ELSIF act IN ('match-confirm', 'match-undo') AND NOT EXISTS (\n"
        "                   SELECT 1 FROM memory_changes ch\n"
        "                   WHERE ch.guild_id = g AND ch.campaign_id = c\n"
        "                   AND ch.source = 'who' AND ch.batch::text = tgt) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'names-fixed-undo' AND NOT EXISTS (",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and two more added at the end, so an
    -- action another migration added stays in the list whatever the order the migrations merge in.
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
            || replace(d, '])', ', ''match-confirm''::text, ''match-undo''::text])');
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

    -- The matches DMbot made by itself that the DM has not answered (#3602): a `who` change that
    -- merged a waiting name's entry into a confirmed one, not undone, whose moved spelling is
    -- still only a suggestion and not secret. The rule is `MemoryStore._moved_by_join`'s, copied
    -- exactly (core/src/dmbot/memory/store.py), so this list and the bot's confirm never differ.
    -- Only a DM of the campaign is given anything: anyone else, a member who is not a DM
    -- included, gets NULL. Names, the batch number and the time only: no line of a transcript,
    -- and never a secret (a match never involves one). Oldest first, at most 200. Nothing is
    -- written.
    CREATE FUNCTION dmbot_web_waiting_matches(g BIGINT, camp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = camp) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = camp
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'matches', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'batch', x.batch, 'keepId', x.keep_id, 'keepName', x.keep_name,
                       'spelling', x.spelling, 'at', x.made_at) ORDER BY x.batch)
            FROM (
                SELECT DISTINCT ON (ch.batch) ch.batch, k.id AS keep_id, k.name AS keep_name,
                       e.name AS spelling, ch.made_at
                FROM memory_changes ch
                JOIN memory_changes m
                  ON m.guild_id = ch.guild_id AND m.campaign_id = ch.campaign_id
                 AND m.batch = ch.batch AND m.table_name = 'memory_aliases' AND m.op = 'update'
                 AND m.before->>'entity_id' = ch.row_id
                 AND m.after->>'entity_id' = ch.after->>'merged_into'
                JOIN memory_aliases a
                  ON a.guild_id = m.guild_id AND a.campaign_id = m.campaign_id AND a.id = m.row_id
                 AND a.entity_id = ch.after->>'merged_into'
                 AND a.status = 'proposed' AND NOT a.secret
                JOIN memory_entities k
                  ON k.guild_id = ch.guild_id AND k.campaign_id = ch.campaign_id
                 AND k.id = ch.after->>'merged_into' AND k.status = 'confirmed'
                JOIN memory_entities e
                  ON e.guild_id = ch.guild_id AND e.campaign_id = ch.campaign_id
                 AND e.id = ch.row_id
                WHERE ch.guild_id = g AND ch.campaign_id = camp AND ch.source = 'who'
                  AND ch.table_name = 'memory_entities' AND ch.op = 'update'
                  AND ch.undoes IS NULL AND ch.after->>'status' = 'merged'
                  AND NOT EXISTS (SELECT 1 FROM memory_changes u
                                  WHERE u.guild_id = ch.guild_id AND u.campaign_id = ch.campaign_id
                                    AND u.undoes = ch.batch)
                ORDER BY ch.batch
                LIMIT 200) x), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_waiting_matches(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_waiting_matches(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_waiting_matches", "dmbot_web_waiting_matches(BIGINT, TEXT)"),
)
