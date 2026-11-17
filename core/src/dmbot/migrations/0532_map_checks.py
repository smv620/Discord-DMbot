"""0532_map_checks

The campaign's "Map checks" switch (#4670, MapBot slice 4c-2; Supervisor's ruling on #4670): whether
DMbot may send the DM a private note during play when a distance or direction the DM says is far off
a kept map's.
"""

from __future__ import annotations

# On by default: the note is the DM's alone, at most once for each pair of places in a session, and
# it only reaches anyone while DMBOT_MAP_MEASURE is on. The note's one button, 🔕 Stop map checks,
# turns it off for the campaign; ⚙️ Settings turns it back on (least privilege: every choice is
# visible and can be undone). A defaulted column changes no rows, so no row-level-security policy
# is needed.
SQL = r"""
    ALTER TABLE campaigns ADD COLUMN map_checks BOOLEAN NOT NULL DEFAULT TRUE;
"""
