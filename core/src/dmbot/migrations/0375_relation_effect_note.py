"""0375_relation_effect_note

Take the "happened to them" note off kept connections' details (#3650).
"""

from __future__ import annotations

# Rows only: no column or table changes. Before #3628 a confirmed claim carried its effect note
# (`voice.EFFECT_NOTE`, "Told by the DM as something that happened to <name>, not as <name>'s own
# choice.") into the connection it made, so a feeling word read "friendly Told by the DM…" and was
# no word at all. Only that exact sentence is taken off, only at the very end, and only when both
# names in it are the same (the same rule as `voice.split_effect_note`); the spaces before it go
# too (the pattern takes them). A detail that was only the note becomes the empty string, which
# every reader of `memory_relations.detail` already treats as "no detail". Any other text, the
# note's words in the middle of a detail included, is left alone. A second run changes nothing.
# A migration that changes rows opens row-level security for itself (CLAUDE.md, #704): the one
# table this reads and writes gets a `migrate_backfill` policy for the length of the migration.
# Claims keep the note on purpose (their readers take it off, #3628); beliefs hold no detail; the
# readers and a restored backup take it off old rows too (`store._relation`, `backup`).
# The count of details changed is raised as a notice, counts only.
NOTE = r"\s*Told by the DM as something that happened to (.+?), not as \1's own choice\.\s*$"

SQL = (
    r"""
    CREATE POLICY migrate_backfill ON memory_relations FOR ALL USING (true) WITH CHECK (true);

    DO $$
    DECLARE
        changed BIGINT;
    BEGIN
        UPDATE memory_relations
        SET detail = regexp_replace(detail, '"""
    + NOTE.replace("'", "''")
    + r"""', '')
        WHERE detail ~ '"""
    + NOTE.replace("'", "''")
    + r"""';
        GET DIAGNOSTICS changed = ROW_COUNT;
        RAISE NOTICE '0375_relation_effect_note: % connection details cleaned', changed;
    END $$;

    DROP POLICY migrate_backfill ON memory_relations;
"""
)
