"""0595_outbox_dedupe

The outbox's "say it once" is kept by the database, not only by a check (#5005, #4904 3b).
"""

from __future__ import annotations

# `Outbox.add(..., unique=True)` used to ask "is the same row already waiting?" and then insert, so
# two writers at the same instant could both pass the question before either inserted. A row now
# says whether it is a dedupe row (`dedupe`, set by `unique=True`) and a partial unique index
# refuses a second dedupe row of the same kind and payload while one waits or is being sent. It is
# per use, not per kind: rows that don't ask for it never conflict. A row that is sent or failed
# leaves the index, so the same offer may come again. Existing rows are not dedupe rows. No row
# changes: only a column with a default and an index.
SQL = """
    ALTER TABLE outbox ADD COLUMN dedupe BOOLEAN NOT NULL DEFAULT false;
    CREATE UNIQUE INDEX outbox_dedupe ON outbox (guild_id, campaign_id, kind, payload)
        WHERE dedupe AND state IN ('waiting', 'sending');
    """
