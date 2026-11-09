"""0396_upload_notices

Lore uploads add what DMbot is sure of by themselves (#3878): the campaign's "Ask me before adding"
setting, and the notice each upload leaves, with the change numbers that "Undo this upload" takes
back.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `ask_before_adding` is off by default: an upload adds what DMbot is sure of and says how many.
# On, today's Add all flow comes back unchanged. A defaulted column changes no rows, so no
# row-level-security policy is needed.
#
# One row for each upload that added things for the DM: its name and date as the DM saw them
# (`label`), the counts by kind (`counts`, for the notice and its "added" words: numbers only), the
# change numbers it made (`changes`, to take back together) and where its live message in the DM
# Bot channel is (`channel_id`, `message_id`, ids only, so the buttons survive a restart, #3874).
# `undone_at` is when the DM took the whole upload back; Undo works for 7 days (the store's own
# limit on taking a change back), after which the button goes.
SQL = r"""
    ALTER TABLE campaigns ADD COLUMN ask_before_adding BOOLEAN NOT NULL DEFAULT FALSE;

    CREATE TABLE upload_notices (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        label       TEXT NOT NULL DEFAULT '' CHECK (char_length(label) <= 200),
        created_at  BIGINT NOT NULL,
        counts      JSONB NOT NULL DEFAULT '{}'::JSONB,
        changes     BIGINT[] NOT NULL DEFAULT '{}',
        channel_id  BIGINT,
        message_id  BIGINT,
        undone_at   BIGINT,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX upload_notices_by_campaign ON upload_notices (guild_id, campaign_id, created_at);
    """ + _isolate("upload_notices")

ISOLATED_TABLES: tuple[str, ...] = ("upload_notices",)
