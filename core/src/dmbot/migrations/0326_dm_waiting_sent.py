"""0326_dm_waiting_sent

Which "things wait for you" private message was already sent for a lore upload or an import
(#3314), so a restart never sends it twice.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

DM_WAITING_SENT = """
    -- One row for each private message DMbot sent a campaign's DMs after a lore upload or an
    -- import finished, saying how many checks wait (names, unnamed people, connections, family).
    -- `key` says which one ("lore:<time>", "import:<id>", "import:<id>:rest"); a second claim of
    -- the same key does nothing, so a restart or a second round never sends it again. Keys, kinds
    -- and counts only, never a name or a word of the text; it is rebuilt by nothing, so it is
    -- left out of backups (a restored campaign starts with no message sent and sends none until
    -- its next upload or import). New table, so no row changes and no backfill policy.
    CREATE TABLE dm_waiting_sent (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        key         TEXT NOT NULL CHECK (key <> '' AND length(key) <= 80),
        sent_at     BIGINT NOT NULL CHECK (sent_at > 0),
        -- what the message offered, as "kind=count" words, for a later message to leave out
        offered     TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (guild_id, campaign_id, key),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("dm_waiting_sent")

SQL = DM_WAITING_SENT

ISOLATED_TABLES: tuple[str, ...] = ("dm_waiting_sent",)
