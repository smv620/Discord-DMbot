"""0611_name_found_typed

How many times a waiting name was heard, and how many of those hearings were typed in chat (#5133
slice 3c), so its card can say "Heard 5 times, …" or "Typed once in chat, and heard 3 times, …", and
the waiting list can be ordered by it, from numbers rather than the note's words.
"""

from __future__ import annotations

SQL = r"""
    -- Counts only, beside `how` (0608): the hearings of the name's group and how many were typed,
    -- never which lines. 0 times: not counted (a row from before), the card reads the note instead.
    -- Added with defaults, not updated, so no row-level security needs opening.
    ALTER TABLE name_found
        ADD COLUMN times INTEGER NOT NULL DEFAULT 0 CHECK (times >= 0),
        ADD COLUMN typed INTEGER NOT NULL DEFAULT 0 CHECK (typed >= 0 AND typed <= times);
    """

ISOLATED_TABLES: tuple[str, ...] = ()
