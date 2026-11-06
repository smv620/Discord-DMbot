"""0046_transcript_entity."""

from __future__ import annotations

TRANSCRIPT_ENTITY = """
    -- Who a line was said as (#53): NULL when nobody is named (the player's character is
    -- shown from the sheet), 'narrating' for the DM's narration, or the confirmed, non-secret
    -- name of the NPC the DM was voicing. Never a secret name: the writer checks that.
    ALTER TABLE transcript_lines
        ADD COLUMN entity TEXT CHECK (entity IS NULL OR length(entity) BETWEEN 1 AND 120);
    """

SQL = TRANSCRIPT_ENTITY
