"""0234_move_cascade_late

Keys of tables made after 0215 cascade too (#2550).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _CASCADE_ALL_KEYS,
)

# The same catalog loop again, for the tables made after 0215 (0225's `recap_upload_rights`); a
# table made later than this declares ON UPDATE CASCADE itself (CLAUDE.md, #2550).
MOVE_CASCADE_LATE = _CASCADE_ALL_KEYS

SQL = MOVE_CASCADE_LATE
