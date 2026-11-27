"""0653_clock_custom_calendar

A campaign's own calendar on its game clock (#5364, slice 2).
"""

from __future__ import annotations

import importlib

# Game time stays one number (minutes since Day 1); a custom calendar only words it. So the
# definition rides on the clock's row, beside the date of Day 1 that Harptos already keeps in
# `calendar_year` and `calendar_ordinal`: one new JSONB column, `calendar_def`, holding
# `{"layout": <the definition>, "weekday": <Day 1's weekday>}` (`timebot.layout.Layout.to_json`),
# and the kind `custom`. A calendar belongs to its campaign: it follows it in a move and in a
# backup (`ClockSection`) and goes with it. No row changes, so no row-level security is opened.
#
# The old checks are replaced: the kind may be `custom`, and a custom year may be up to 1,000 days
# (the ordinal, counted from 0, up to 999; Harptos's own 365 is checked in code by `DayOne`). The
# size of the definition is capped (40 parts of 30-letter names is a few thousand bytes). The old
# checks are found by their column rather than by name, so a differently named one cannot stay.
SQL = r"""
    ALTER TABLE game_clocks
        ADD COLUMN calendar_def JSONB
            CHECK (calendar_def IS NULL
                   OR (jsonb_typeof(calendar_def) = 'object'
                       AND pg_column_size(calendar_def) <= 16000));
    DO $do$
    DECLARE
        c RECORD;
    BEGIN
        FOR c IN
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
            WHERE con.conrelid = 'game_clocks'::regclass AND con.contype = 'c'
              AND cardinality(con.conkey) = 1
              AND a.attname IN ('calendar_kind', 'calendar_ordinal')
        LOOP
            EXECUTE format('ALTER TABLE game_clocks DROP CONSTRAINT %I', c.conname);
        END LOOP;
    END
    $do$;
    ALTER TABLE game_clocks DROP CONSTRAINT game_clocks_calendar_date;
    ALTER TABLE game_clocks
        ADD CONSTRAINT game_clocks_calendar_kind_check
            CHECK (calendar_kind IN ('days', 'harptos', 'custom')),
        ADD CONSTRAINT game_clocks_calendar_ordinal_check
            CHECK (calendar_ordinal IS NULL OR calendar_ordinal BETWEEN 0 AND 999),
        ADD CONSTRAINT game_clocks_calendar_date
            CHECK ((calendar_year IS NULL) = (calendar_ordinal IS NULL)
                   AND (calendar_kind = 'days' OR calendar_year IS NOT NULL)
                   AND ((calendar_kind = 'custom') = (calendar_def IS NOT NULL)));
"""

# The website's clock door (0516) also gives the definition, so its page can word a countdown in
# the campaign's calendar. Rebuilt from 0516's own text so the DM-only check cannot drift, with
# each replacement asserted. The search path is set again, as every replaced door does.
_OLD = importlib.import_module("dmbot.migrations.0516_game_clock_door")
_FROM = "CREATE FUNCTION dmbot_web_game_clock("
_KEYS = "'ordinal', clock.calendar_ordinal);"
assert _OLD.SQL.count(_FROM) == 1 and _OLD.SQL.count(_KEYS) == 1
CLOCK_DOOR = _OLD.SQL.replace(_FROM, "CREATE OR REPLACE FUNCTION dmbot_web_game_clock(").replace(
    _KEYS, "'ordinal', clock.calendar_ordinal,\n            'def', clock.calendar_def);"
)

SQL = SQL + CLOCK_DOOR

ISOLATED_TABLES: tuple[str, ...] = ()
# (the door keeps its place in the web role's list: 0516 registered it)
