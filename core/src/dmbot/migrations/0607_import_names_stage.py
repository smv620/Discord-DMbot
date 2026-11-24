"""0607_import_names_stage

The name reader's stage on the import card (#5091, Supervisor's ruling): where the after-session
name reader (docs/design/session_names.md, slice 2) is while an imported session's names are read.
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# The import card already shows the story read's stage from `session_imports` (0325: state, part of
# parts, started, moved); the name reader gets the same, as its own stage, so a long read is never a
# silent wait (CLAUDE.md, "Every wait shows that DMbot is still working", #2564). Counts and times
# only, never a name: `names_state` is '' (not read, or the reader is off), 'reading', 'read', or
# 'stopped' (the reader failed after it started: the card says so, and the names come from the scan
# alone; a wait that stops is never left looking as if it still runs);
# `names_part` of `names_parts` is the stretch being read; `names_started_at` / `names_moved_at` are
# Unix seconds; `names_unread` is how many stretches could not be read (said plainly, never "no
# names"). `dmbot_import_progress` sends them beside the story's, rebuilt from its stored definition
# by guarded replacement (a drift stops the migration), as 0602 did, so its search path and
# privileges stay.
_IMPORT_SWAPS = (
    (
        "'parts', i.story_parts, 'started', i.story_started_at,\n"
        "                           'moved', i.story_moved_at),",
        "'parts', i.story_parts, 'started', i.story_started_at,\n"
        "                           'moved', i.story_moved_at),\n"
        "                       'names', jsonb_build_object(\n"
        "                           'state', i.names_state, 'part', i.names_part,\n"
        "                           'parts', i.names_parts, 'started', i.names_started_at,\n"
        "                           'moved', i.names_moved_at, 'unread', i.names_unread),",
    ),
)

SQL = (
    r"""
    ALTER TABLE session_imports
        ADD COLUMN names_state      TEXT NOT NULL DEFAULT ''
            CHECK (names_state IN ('', 'reading', 'read', 'stopped')),
        ADD COLUMN names_part       INTEGER NOT NULL DEFAULT 0 CHECK (names_part >= 0),
        ADD COLUMN names_parts      INTEGER NOT NULL DEFAULT 0 CHECK (names_parts >= 0),
        ADD COLUMN names_started_at BIGINT NOT NULL DEFAULT 0 CHECK (names_started_at >= 0),
        ADD COLUMN names_moved_at   BIGINT NOT NULL DEFAULT 0 CHECK (names_moved_at >= 0),
        ADD COLUMN names_unread     INTEGER NOT NULL DEFAULT 0 CHECK (names_unread >= 0);

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
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
