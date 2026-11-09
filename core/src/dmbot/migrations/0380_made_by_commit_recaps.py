"""0380_made_by_commit_recaps

Data lineage, part 2 (#3739): recaps and the theatre script record the build that made
each row.
"""

from __future__ import annotations

# Part 2 of #3739 (see `dmbot.lineage` and 0378, which made the trigger function). Nothing here
# changes a row: the column is NULL for everything made before this. The tables are the ones DMbot
# generates in the recap area: the job (its draft, title, summary and audio state), the dictionaries
# the narration uses, a DM's instructions request (it holds DMbot's
# answer), the accepted versions (the build that kept the text, as the DM pressed accept), and the
# theatre script. `recap_holds`, `recap_name_checks` (ids only) and `theatre_runs` (counts)
# are bookkeeping and stay off (`lineage.NOT_STAMPED`).
TABLES = (
    "recap_jobs",
    "recap_dictionaries",
    "recap_requests",
    "recap_accepted",
    "theatre_lines",
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

    -- The voiceover has its own stamp (#3739): a later build narrating an older text must not
    -- overwrite the text's. Set when the audio is finished, NULL on a failure or a re-queue.
    ALTER TABLE recap_jobs ADD COLUMN audio_made_by_commit TEXT
        CONSTRAINT recap_jobs_audio_made_by_commit_check
        CHECK (audio_made_by_commit ~ '^[0-9a-f]{40}$');
"""
)
