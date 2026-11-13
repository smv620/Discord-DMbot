"""0434_link_undo

The website can take back a connection edit (#4156, for the Story Graph panel's ↩ Undo): the
queue accepts `link-undo`, and an edit's answer carries the change batch it made.
"""

from __future__ import annotations

# `link-undo` (target: the change batch, in digits) takes one connection edit back with the store's
# own undo, as Discord's Undo does. The door only lets a DM queue it for a batch that one of their
# own done `link-*` edits made in this campaign (`result_batches`), and the bot checks again when it
# applies it (a made-up number must never reach `undo`, which would undo any change). The door is
# read back from the database as it is (many migrations have changed it) and each anchor must be
# found the exact number of times, so a drift stops the migration instead of widening the wrong
# rule, as 0369 does. The read door also returns the row's batches, which an edit's answer carries
# to the page. No row is changed.
_SWAPS = (
    # `link-undo` is not an edit of a connection (its target is a batch, not a connection)
    (
        "is_link BOOLEAN := act LIKE 'link-%';",
        "is_link BOOLEAN := act LIKE 'link-%' AND act <> 'link-undo';",
        1,
    ),
    # the allowed actions
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                'link-who',",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin', 'link-undo',\n"
        "                'link-who',",
        1,
    ),
    # no words with it
    (
        "'remove', 'link-secret', 'link-remove', 'link-pin',\n"
        "                       'claim-confirm',",
        "'remove', 'link-secret', 'link-remove', 'link-pin', 'link-undo',\n"
        "                       'claim-confirm',",
        1,
    ),
    # a target is needed, and nothing else may carry one (both lists)
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        "                       'claim-confirm',",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin', 'link-undo',\n"
        "                       'claim-confirm',",
        2,
    ),
    # the target is a batch number
    (
        "WHEN act IN ('names-fixed-undo', 'match-confirm', 'match-undo') THEN",
        "WHEN act IN ('names-fixed-undo', 'match-confirm', 'match-undo', 'link-undo') THEN",
        1,
    ),
    # no secret/remove flag
    (
        "'claim-pick', 'claim-unpick', 'claim-undo', 'name', 'name-join', 'name-undo',"
        " 'spelling-yes', 'spelling-no',\n",
        "'claim-pick', 'claim-unpick', 'claim-undo', 'link-undo', 'name', 'name-join',"
        " 'name-undo', 'spelling-yes', 'spelling-no',\n",
        1,
    ),
    # it goes through the same checks as the other undo actions
    (
        "OR act IN ('match-confirm', 'match-undo')\n",
        "OR act IN ('match-confirm', 'match-undo', 'link-undo')\n",
        1,
    ),
    # an undo needs a batch this DM's own done edit of this campaign made
    (
        "ELSIF act = 'claim-pick' AND NOT EXISTS (",
        "ELSIF act = 'link-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action LIKE 'link-%' AND p.action <> 'link-undo'\n"
        "                   AND p.state = 'done'\n"
        "                   AND tgt::bigint = ANY (p.result_batches)) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'claim-pick' AND NOT EXISTS (",
        1,
    ),
)

_READ_SWAPS = (
    (
        "'resultOther', r.result_other,",
        "'resultOther', r.result_other, 'resultBatches', to_jsonb(r.result_batches),",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and one more added at the end, so an
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
            || replace(d, '])', ', ''link-undo''::text])');
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
    + _steps(_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_read';"""
    + _steps(_READ_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
