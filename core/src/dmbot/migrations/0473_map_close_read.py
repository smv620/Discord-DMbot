"""0473_map_close_read

A map note keeps the close read of a map DMbot may use (#4173 slice 6b-1).
"""

from __future__ import annotations

# Supervisor's ruling on slice 6b (2026-11-13): a map DMbot may use (`map_use.may_use_map`: today,
# one the campaign's DM posted) is read closely, and that read is kept with its note: how much the
# map shows (`level`), each place's size and form, and how pairs of drawn places sit (`links`: in,
# next_to, route_to or apart, each with its word and how sure). NULL for every note that had no
# close read (all of them so far, and a player's map), so no row changes and no row-level security
# needs opening. Nothing is written to the story from it yet (6b-2). Carried in the map-notes
# backup section; words and names only, as the rest of the note.
SQL = r"""
    ALTER TABLE map_notes ADD COLUMN close_read JSONB;
"""
