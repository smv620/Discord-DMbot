"""0408_session_notes_fill

A finished recap kept by the test run (`recap_run --keep`, the sample run) skipped the step that
keeps its beat sheet as the session's notes, so its Session notes tab was empty (#3959). The code
now keeps them; this gives the ones already kept their notes from the beats their job still holds.
"""

from __future__ import annotations

# A migration that changes rows opens row-level security for itself (CLAUDE.md, #704): both tables
# are touched below, so each has a `migrate_backfill` policy for the length of the file.
# Only `ready` is filled: the one path that skipped the notes (`add_finished`) always writes a
# ready job, and the pipeline's unfinished jobs keep theirs as they go.
# Only a ready recap with a beat sheet and no notes yet gains a row; a session that has notes is
# left as it is (ON CONFLICT DO NOTHING), and the beats are copied as they are, never rewritten.
SQL = """
    CREATE POLICY migrate_backfill ON session_notes FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON recap_jobs FOR SELECT USING (true);

    INSERT INTO session_notes
            (guild_id, campaign_id, session_at, beats, written_at, job_created_at)
        SELECT j.guild_id, j.campaign_id, j.session_at, j.beats, j.updated_at, j.created_at
        FROM recap_jobs j
        WHERE jsonb_typeof(j.beats) = 'array' AND jsonb_array_length(j.beats) > 0
          AND j.state = 'ready'
        ON CONFLICT DO NOTHING;

    DROP POLICY migrate_backfill ON recap_jobs;
    DROP POLICY migrate_backfill ON session_notes;
"""

ISOLATED_TABLES: tuple[str, ...] = ()
