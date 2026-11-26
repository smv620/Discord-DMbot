"""0571_worker_leases

One lease row for each kind of background work (#3842; docs/PLAN.md, "A worker container, and a
smaller bot.py"), so "one recap's audio at a time" holds across processes (a bot and a worker, or
two workers), not only inside one.
"""

from __future__ import annotations

# The table holds a kind word ('audio'), a random holder token and a time: no server, campaign or
# person, so it is an unscoped table like `recap_audio_strays` (`schema.UNSCOPED_TABLES`). The
# website role is given nothing on it. A holder claims it with one conditional upsert (free, expired
# or its own), renews it while it works, and gives it back at the end; a holder that dies is
# replaced once `leased_until` passes.
SQL = """
    CREATE TABLE worker_leases (
        kind         TEXT PRIMARY KEY CHECK (kind ~ '^[a-z_]{1,40}$'),
        holder       TEXT NOT NULL,
        leased_until BIGINT NOT NULL
    );
    """
