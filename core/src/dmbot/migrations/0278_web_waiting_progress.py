"""0278_web_waiting_progress

An answer on "Waiting for you" shows how far it has got (#2921).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _swaps,
)

# CLAUDE.md, "Every wait shows that DMbot is still working" (#2564): an answer to a waiting fact,
# connection or question is queued in `name_actions` for the bot, and the page now shows the stage,
# how long it has waited and when it last moved (from the times the row already has), and, for Keep
# all, "12 of 50". The count needs three small columns the bot writes as it goes: how many of the
# connections it will keep (`progress_total`), how many it has kept (`progress_done`) and when it
# last moved (`progress_at`, Unix seconds). Counts and times only: no words. A row nobody has
# written progress to reads 0 of 0, which the website shows as no count. The answer door is read
# back from the database as it is and widened, each anchor found exactly once (a drift stops the
# migration), as 0266, 0270 and 0275 do. No row changes.
_PROGRESS_SWAPS = (
    (
        "'createdAt', r.created_at, 'claimedAt', r.claimed_at,",
        "'createdAt', r.created_at, 'claimedAt', r.claimed_at,\n"
        "                                  'progressDone', r.progress_done,"
        " 'progressTotal', r.progress_total,\n"
        "                                  'progressAt', r.progress_at,",
    ),
)

WEB_WAITING_PROGRESS = (
    r"""
    ALTER TABLE name_actions
        ADD COLUMN progress_done  INT NOT NULL DEFAULT 0 CHECK (progress_done >= 0),
        ADD COLUMN progress_total INT NOT NULL DEFAULT 0 CHECK (progress_total >= 0),
        ADD COLUMN progress_at    BIGINT NOT NULL DEFAULT 0,
        ADD CONSTRAINT name_actions_progress_check CHECK (progress_done <= progress_total);

    COMMENT ON COLUMN name_actions.progress_done IS
        'Connections a Keep all has kept so far. Written with progress_total in ONE update (the'
        ' CHECK needs done <= total), and set back to 0 when a stalled row is retaken.';
    COMMENT ON COLUMN name_actions.progress_total IS
        'Connections a Keep all will try (at most 50); 0 when no count is kept.';
    COMMENT ON COLUMN name_actions.progress_at IS
        'When the bot last moved this row forward, in Unix SECONDS (claimed_at is milliseconds).';

    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
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
              AND p.proname = 'dmbot_web_name_action_read';
"""
    + _swaps("def", _PROGRESS_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

SQL = WEB_WAITING_PROGRESS
