"""0343_claim_pick_actions

The queue accepts a "Which one?" answer and its Undo from the website (#3463).
"""

from __future__ import annotations

# `claim-pick` (target: the claim, text: `subject:<entry id>` or `object:<entry id>`) answers a kept
# claim's "Which one?"; `claim-unpick` (target: the claim) takes the answer back while the claim
# still waits. "None of these" is the existing `claim-reject`. The door is read back from the
# database as it is (many migrations have changed it) and each anchor must be found the exact
# number of times, so a drift stops the migration instead of widening the wrong rule, as 0341 does.
# The door checks what it can see: the claim waits in this campaign and, for a pick, is still kept
# for that answer; the bot checks the choice itself when it applies it. No row is changed.
UNPLACED_REASON = "kept for your answer: a name in it could be two different entries"

_SWAPS = (
    # the allowed actions
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',"
        " 'backstory-keep-all',",
        "'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',"
        " 'backstory-skip', 'backstory-keep-all',",
        1,
    ),
    # no text for an Undo
    (
        "'backstory-skip',\n                       'backstory-which-neither',\n",
        "'backstory-skip', 'claim-unpick',\n                       'backstory-which-neither',\n",
        1,
    ),
    # both target lists (needs a claim id)
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick'",
        "'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',"
        " 'backstory-skip',\n                       'backstory-which-pick'",
        2,
    ),
    # the pick's text
    (
        "OR (act = 'confirm-undo' AND clean <> '')",
        "OR (act = 'confirm-undo' AND clean <> '')\n"
        "           OR (act = 'claim-pick' AND clean !~ '^(subject|object):[0-9a-f]{32}$')",
        1,
    ),
    # no secret/remove flag
    (
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'name', 'name-join', 'name-undo')",
        "'reading-place', 'reading-no', 'reading-undo', 'names-fixed-undo',\n"
        "                       'claim-pick', 'claim-unpick', 'name', 'name-join', 'name-undo')",
        1,
    ),
    # a pick needs a claim that is still kept for the answer
    (
        "ELSIF act IN ('reading-place', 'reading-no') AND NOT EXISTS (",
        "ELSIF act = 'claim-pick' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM memory_claims m\n"
        "                   WHERE m.guild_id = g AND m.campaign_id = c\n"
        "                   AND m.id = tgt AND m.status = 'proposed'\n"
        "                   AND m.reason = '" + UNPLACED_REASON + "') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act IN ('reading-place', 'reading-no') AND NOT EXISTS (",
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
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',
         'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',
         'backstory-skip', 'backstory-keep-all', 'backstory-which-pick',
         'backstory-which-neither', 'reading-place', 'reading-no', 'reading-undo',
         'names-fixed-undo'));

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
