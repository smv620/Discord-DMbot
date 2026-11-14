"""0486_route_length

A route's stated length (#4255, MapBot layers slice 1; Supervisor's ruling on #4255, 2026-11-14).
"""

from __future__ import annotations

# The length as the source gave it, on a `route_to` connection and on a route place (`form` =
# 'route'), never measured from a picture (design basis §4). `length_words` is always kept and is
# what the card shows; `length_amount` and `length_unit` (hours, days or miles) only when the words
# give them plainly (`memory/route_length.py`); `length_approx` when the words say "about" or give
# a range (the lower end is the amount), so every answer says "about" too; `length_from` is who
# gave it: DMbot's read (the DM's to keep; players never see it until then) or the DM. All five are
# NULL together for no length. Carried by the change log, merges and backups like the rest of the
# row. No row is changed.
_COLUMNS = r"""
        ADD COLUMN length_words TEXT CHECK (char_length(length_words) BETWEEN 1 AND 60),
        ADD COLUMN length_amount DOUBLE PRECISION
            CHECK (length_amount > 0 AND length_amount <= 10000),
        ADD COLUMN length_unit TEXT CHECK (length_unit IN ('hours', 'days', 'miles')),
        ADD COLUMN length_approx BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN length_from TEXT CHECK (length_from IN ('read', 'dm'))"""


def _pairs(table: str) -> str:
    return f"""
    ALTER TABLE {table} ADD CONSTRAINT {table}_length_together CHECK (
        (length_words IS NULL) = (length_from IS NULL)
        AND (length_amount IS NULL) = (length_unit IS NULL)
        AND (length_words IS NOT NULL OR (length_amount IS NULL AND NOT length_approx)));"""


SQL = (
    "\n    ALTER TABLE memory_relations"
    + _COLUMNS
    + ";\n    ALTER TABLE memory_entities"
    + _COLUMNS
    + ";"
    + _pairs("memory_relations")
    + _pairs("memory_entities")
    + """
    ALTER TABLE memory_relations ADD CONSTRAINT memory_relations_length_on_a_route
        CHECK (length_words IS NULL OR predicate = 'route_to');
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_length_on_a_route
        CHECK (length_words IS NULL OR form IS NOT DISTINCT FROM 'route');  -- (NULL is no route)
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
