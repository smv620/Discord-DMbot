"""0369_claim_undo_action

The queue accepts the Undo of an "All true" / "All secret" press from the website (#3645).
"""

from __future__ import annotations

# `claim-undo` (target: the claim, text: the change batch of the DM's own `claim-confirm` of it)
# takes one kept fact back and asks the question again, the calls of Discord's "Undo all"
# (`memory.undo` with `ClaimStore.reopen_in`). The door only lets a DM queue it for a batch their
# own done `claim-confirm` of that claim produced (as `reading-undo` does for a reading); the bot
# checks again when it applies it. The door is read back from the database as it is (many migrations
# have changed it) and each anchor must be found the exact number of times, so a drift stops the
# migration instead of widening the wrong rule, as 0343 does. No row is changed.
_SWAPS = (
    # the allowed actions
    (
        "'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',"
        " 'backstory-skip', 'backstory-keep-all',",
        "'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'claim-undo',"
        " 'backstory-keep', 'backstory-skip', 'backstory-keep-all',",
        1,
    ),
    # the batch's shape (digits, as an Undo number)
    (
        "OR (act = 'claim-pick' AND clean !~ '^(subject|object):[0-9a-f]{32}$')",
        "OR (act = 'claim-pick' AND clean !~ '^(subject|object):[0-9a-f]{32}$')\n"
        "           OR (act = 'claim-undo' AND clean !~ '^[0-9]{1,12}$')",
        1,
    ),
    # both target lists (needs a claim id)
    (
        "'claim-pick', 'claim-unpick', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick'",
        "'claim-pick', 'claim-unpick', 'claim-undo', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick'",
        2,
    ),
    # no secret/remove flag
    (
        "'claim-pick', 'claim-unpick', 'name', 'name-join', 'name-undo', 'spelling-yes',"
        " 'spelling-no',",
        "'claim-pick', 'claim-unpick', 'claim-undo', 'name', 'name-join', 'name-undo',"
        " 'spelling-yes', 'spelling-no',",
        1,
    ),
    # the other claim answers need a waiting claim; an Undo needs the DM's own done confirm of it
    (
        "IF act LIKE 'claim-%' AND NOT EXISTS (",
        "IF act LIKE 'claim-%' AND act <> 'claim-undo' AND NOT EXISTS (",
        1,
    ),
    (
        "ELSIF act = 'claim-pick' AND NOT EXISTS (",
        "ELSIF act = 'claim-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.target_id = tgt AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action = 'claim-confirm' AND p.state = 'done'\n"
        "                   AND p.result_other = clean) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'claim-pick' AND NOT EXISTS (",
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
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other',
         'secret', 'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words',
         'link-secret', 'link-remove', 'link-pin', 'link-who', 'alias-who',
         'confirm-kinds', 'confirm-undo', 'claim-confirm', 'claim-reject', 'claim-pick',
         'claim-unpick', 'claim-undo', 'backstory-keep', 'backstory-skip',
         'backstory-keep-all', 'backstory-which-pick', 'backstory-which-neither',
         'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',
         'spelling-yes', 'spelling-no', 'told-add', 'told-remove', 'players-line'));

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
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
