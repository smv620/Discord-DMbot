"""0351_load_counts

Words and AI calls so far for a session import and a lore upload (#3534).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# The owner asked for live counts while a lore upload or a session import loads. Two counts the bot
# can add cheaply, in the write it already makes once per chunk: `words_done` (words handled so far,
# counted from the text and never stored) and `calls_made` (AI calls so far). Counts only, never a
# word of a document or a transcript. The columns have defaults, so no row changes and no backfill
# policy is needed; the import's `updated_at` trigger already fires on `step_done`, which moves in
# the same statement. The two read doors (`dmbot_import_progress`, 0217; `dmbot_web_lore_read`, 0288
# and 0315) are rebuilt from their stored definitions by guarded replacement (a drift stops the
# migration), so the search path and privileges they have now stay.
_IMPORT_SWAPS = (
    (
        "'stepDone', i.step_done, 'stepTotal', i.step_total,",
        "'stepDone', i.step_done, 'stepTotal', i.step_total,\n"
        "                       'wordsDone', i.words_done, 'callsMade', i.calls_made,",
    ),
)
_LORE_SWAPS = (
    (
        "'createdAt', r.created_at, 'updatedAt', r.updated_at,",
        "'wordsDone', r.words_done, 'callsMade', r.calls_made,\n"
        "            'createdAt', r.created_at, 'updatedAt', r.updated_at,",
    ),
)

LOAD_COUNTS = (
    r"""
    ALTER TABLE session_imports
        ADD COLUMN words_done INT NOT NULL DEFAULT 0 CHECK (words_done >= 0),
        ADD COLUMN calls_made INT NOT NULL DEFAULT 0 CHECK (calls_made >= 0);
    ALTER TABLE lore_uploads
        ADD COLUMN words_done INT NOT NULL DEFAULT 0 CHECK (words_done >= 0),
        ADD COLUMN calls_made INT NOT NULL DEFAULT 0 CHECK (calls_made >= 0);

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
              AND p.proname = 'dmbot_import_progress';
"""
    + _swaps("def", _IMPORT_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_lore_read';
"""
    + _swaps("def", _LORE_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = LOAD_COUNTS

ISOLATED_TABLES: tuple[str, ...] = ()
