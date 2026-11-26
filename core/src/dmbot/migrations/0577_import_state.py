"""0577_import_state

What an import's scans leave for the bot's one end-of-import report (#4904, step 1 of moving the
import's paid work to the worker; docs/PLAN.md, "A worker container, and a smaller bot.py").
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Until now this was kept in the bot's memory (names the scans added, sessions whose name check was
# given up, how many sessions the story read still has), so a worker that scans could not hand it to
# the bot that reports, and a restart lost it. One row for each (campaign, kind): `data` is JSON of
# counts, ids and the names the scans added (names are the DM's story words, held as every name is:
# in this campaign's own rows, with forced row-level security). A row is taken (deleted and
# returned) when the report is made, so it never outlives its import. Gone with the campaign; not in
# a backup (an import's report is for now). Isolated by server, the key moves with the campaign.
SQL = """
    CREATE TABLE import_state (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind ~ '^[a-z_]{1,40}$'),
        data        JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, kind),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("import_state")

ISOLATED_TABLES: tuple[str, ...] = ("import_state",)
