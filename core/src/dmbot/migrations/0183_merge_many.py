"""0183_merge_many

Merge several names (#2426).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    ALL_NAMES_MENTIONS,
    MERGE_MANY,
)

SQL = MERGE_MANY + ALL_NAMES_MENTIONS

ISOLATED_TABLES: tuple[str, ...] = ("name_not_same",)
