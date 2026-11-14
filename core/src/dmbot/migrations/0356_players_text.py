"""0356_players_text

A players' line for each entry (#2967).
"""

from __future__ import annotations

PLAYERS_TEXT = """
    -- One short line saying what the players know an entry is or does ("the lantern glows green
    -- when a monstrosity is near"), separate from the DM's own `description`, which can hold
    -- spoilers and never feeds it (#2967). Set by the DM today; a "no spoilers" upload may fill it
    -- later. Shown to a player only where the one gate lets them see the entry under its main
    -- name, and only after the same name guard a connection's words get (`memory/gate.py`), so it
    -- never names anything they may not be told. NULL: none. In the undo log, backups and moves
    -- with the entry's other columns.
    ALTER TABLE memory_entities
        ADD COLUMN players_text TEXT CHECK (players_text IS NULL
            OR char_length(players_text) BETWEEN 1 AND 120);
"""

SQL = PLAYERS_TEXT

ISOLATED_TABLES: tuple[str, ...] = ()
