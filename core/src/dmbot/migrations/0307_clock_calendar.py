"""0307_clock_calendar

The campaign's calendar (#3091, part 3).
"""

from __future__ import annotations

CLOCK_CALENDAR = """
    -- Which calendar gives the game clock's days their words, and the date of Day 1 in it. 'days'
    -- is "Day 12" (the default, and what every clock had); 'harptos' is a Faerunian date such as
    -- "Flamerule 14th, 1492 DR". The date of Day 1 is a year and the day of that year (0 is
    -- Hammer 1) as two numbers: words and numbers only, never a real-world TIMESTAMP, and
    -- flashbacks stay on the one timeline. One calendar per campaign, on its clock's row, so it
    -- follows the campaign (a move, a backup) with the clock and is deleted with it. A new column
    -- with a default changes no row.
    ALTER TABLE game_clocks
        ADD COLUMN calendar_kind TEXT NOT NULL DEFAULT 'days'
            CHECK (calendar_kind IN ('days', 'harptos')),
        ADD COLUMN calendar_year INTEGER
            CHECK (calendar_year IS NULL OR calendar_year BETWEEN 1 AND 9999),
        ADD COLUMN calendar_ordinal INTEGER
            CHECK (calendar_ordinal IS NULL OR calendar_ordinal BETWEEN 0 AND 365),
        ADD CONSTRAINT game_clocks_calendar_date
            CHECK ((calendar_year IS NULL) = (calendar_ordinal IS NULL)
                   AND (calendar_kind = 'days' OR calendar_year IS NOT NULL));
"""

SQL = CLOCK_CALENDAR

ISOLATED_TABLES: tuple[str, ...] = ()
