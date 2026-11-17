"""0525_progress_clue_found

A session's "clue found" (#4624, quests slice 6c; design `docs/design/quests.md` §7 and
Supervisor's ruling on #4624): the session reader's progress lines (0520) take one more kind,
`clue`, waiting for the DM like the rest.
"""

from __future__ import annotations

# - `kind` 'clue': the table found a clue to the line's thread. `clue_id` is the campaign's clue
#   when the reader named one it was shown; with none, `words` are the new clue's few words.
#   `state` is 'found' (0520's rule that every line but a new part has a state).
# - `finder_ids`: the characters who found it (entry ids, the names said, or else the characters
#   of the quoted lines' speakers). Kept, each learns the clue; a gone one is skipped.
# A clue deleted since takes its waiting line (`ON DELETE CASCADE`), and the line follows a
# campaign moved to another server (`ON UPDATE CASCADE`, #2550). No existing row changes: the old
# kinds keep their meaning, and the new columns are empty on them.
SQL = r"""
    ALTER TABLE thread_progress DROP CONSTRAINT thread_progress_kind_check;
    ALTER TABLE thread_progress ADD CONSTRAINT thread_progress_kind_check
        CHECK (kind IN ('part', 'part_state', 'thread_state', 'clue'));
    ALTER TABLE thread_progress ADD COLUMN clue_id TEXT;
    ALTER TABLE thread_progress ADD CONSTRAINT thread_progress_clue_fk
        FOREIGN KEY (guild_id, campaign_id, clue_id)
        REFERENCES memory_entities (guild_id, campaign_id, id)
        ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE thread_progress ADD COLUMN finder_ids TEXT[] NOT NULL DEFAULT '{}';
    ALTER TABLE thread_progress ADD CONSTRAINT thread_progress_clue_shape
        CHECK ((kind = 'clue') = (state = 'found')
               AND (kind = 'clue' OR (clue_id IS NULL AND cardinality(finder_ids) = 0))
               AND (kind <> 'clue' OR clue_id IS NOT NULL OR words <> ''));
"""
