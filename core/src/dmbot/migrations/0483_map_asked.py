"""0483_map_asked

When a picture's poster was asked "Is this a map you're allowed to use and save?", and when they
were reminded (MapBot slice 3b-2b, #4344; Supervisor's ruling on #4344). Two nullable times on
`campaign_pictures`, no new table: `map_asked_at` is set when the question is sent, and
`map_reminded_at` when the one reminder at the next session start has gone, so a poster who never
answered is reminded once and never again. A third column, `map_taken_back_batch`, is the change-log
batch of the poster's own "Take it back": the poster's Undo accepts only that batch, so it can
never undo a DM's Remove, Yes or No (Supervisor's review of #4390). Times and a batch number only:
no ID, no words. No existing row changes
(all start NULL), so no row-level security needs opening.
"""

from __future__ import annotations

SQL = r"""
    ALTER TABLE campaign_pictures
        ADD COLUMN map_asked_at BIGINT CHECK (map_asked_at > 0),
        ADD COLUMN map_reminded_at BIGINT CHECK (map_reminded_at > 0),
        ADD COLUMN map_taken_back_batch BIGINT CHECK (map_taken_back_batch > 0);
"""
