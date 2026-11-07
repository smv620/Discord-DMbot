"""0381_made_by_commit_transcripts

Data lineage, part 3 (#3739): transcripts, imports and the notes the story is read from record the
build that made them.
"""

from __future__ import annotations

# Part 3 of #3739 (see `dmbot.lineage` and 0378, which made the trigger function). Nothing here
# changes a row: the column is NULL for everything made before this. Stamped: the transcript's
# sessions and lines (what was heard and cleaned), the import records, the "say it this way"
# offers and the session notes (the beat sheet). Not stamped (see `lineage.NOT_STAMPED`): a
# player's suggested fix, the respell positions of a DM's fix, the DM's choices of who plays whom
# and who a label was (`transcript_suggestions`, `transcript_respells`, `import_plays`,
# `import_speakers`).
TABLES = (
    "transcript_sessions",
    "transcript_lines",
    "session_imports",
    "said_as_suggestions",
    "session_notes",
)

SQL = (
    r"""
    DO $$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY["""
    + ", ".join(f"'{t}'" for t in TABLES)
    + r"""] LOOP
            EXECUTE format(
                'ALTER TABLE %I ADD COLUMN made_by_commit TEXT'
                ' CONSTRAINT %I CHECK (made_by_commit ~ ''^[0-9a-f]{40}$'')',
                t, t || '_made_by_commit_check');
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW'
                ' EXECUTE FUNCTION dmbot_stamp_commit()',
                t || '_stamp_commit', t);
        END LOOP;
    END $$;
"""
)
