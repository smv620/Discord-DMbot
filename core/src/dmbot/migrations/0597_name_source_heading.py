"""0597_name_source_heading

The lore section a name was read under (#5050; Supervisor's ruling on GraphEngineer's design note).
"""

from __future__ import annotations

# A character's card says where they were first met: "In your lore: *Icewind Dale notes*, under
# 'Bryn Shander'". The document's label is already here (0402); its heading is not, and the text
# itself is gone a day after the upload (`lore_queue.KEEP_FOR_S`), so the heading is kept as the
# reader makes the entry (`lore_knowledge.first_headings`). It is the DM's own document's words, so
# the same rule as the label holds: the DM's alone, never a player's door, and not carried in a
# backup (this table is provenance, `campaigns/tables.py`). A row from before this reads "" ("under"
# is left out). At most `lore_knowledge.SECTION_MAX` characters: a longer line is no heading. No row
# changes: a column with a default.
SQL = """
    ALTER TABLE memory_name_sources ADD COLUMN heading TEXT NOT NULL DEFAULT ''
        CHECK (char_length(heading) <= 60);
    """
