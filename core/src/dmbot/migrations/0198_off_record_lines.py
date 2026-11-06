"""0198_off_record_lines

The "off the record" marker line (#2466).
"""

from __future__ import annotations

OFF_RECORD_LINES = """
    -- A stretch where a person was off the record (#2466) is one marker line in the transcript,
    -- in both versions: "(Mia was off the record, nothing recorded, 20:14-20:19 UTC)". It is
    -- stored as a line of its
    -- own kind under that person (so a consent stop or "Delete my past transcripts" takes it
    -- with their other lines) and holds no speech: when it began, how long it lasted, and the
    -- words "off the record". The one change is the list of kinds the column accepts.
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_check
        CHECK (sidebar IN ('question', 'answer', 'table_question', 'off_record'));
"""

SQL = OFF_RECORD_LINES
