"""0320_picture_sweep_actions

The website's picture actions queue, and the bot's leftover-file sweep read (#3274).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

PICTURE_SWEEP_ACTIONS = (
    r"""
    -- What a poster or a DM chose on the website's Pictures page (#3274), waiting for the bot:
    -- make a picture private or public, mark it permanent or not, delete it. The website has no
    -- right on the picture table, so its door only checks who is signed in and queues the choice
    -- here; the bot applies it through `PictureStore` (the per-campaign lock, the 80% permanent
    -- limit and the file deletion live there), after checking again from the database who may do
    -- it. Applied in the order made (`seq`). `note` is what the person sees afterwards, the
    -- refusal words included. A row lives an hour at most, holds no picture words, is isolated
    -- per server, left out of backups (it is a request, not content) and goes with its campaign.
    CREATE TABLE picture_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        picture_id  BIGINT NOT NULL CHECK (picture_id > 0),
        action      TEXT NOT NULL CHECK (action IN
            ('public', 'private', 'permanent', 'unpermanent', 'delete')),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'refused', 'failed')),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX picture_actions_open ON picture_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("picture_actions")
    + r"""
    -- The leftover-file sweep (#3274) compares the bucket's files with every live picture's keys,
    -- across all servers, so it must read them without a server set. Like the other narrow
    -- sweeps (Database.cleanup), the table opens to a read of the rows only inside a transaction
    -- that set `dmbot.picture_sweep` (Database.picture_sweep()). The website's role has no right
    -- on either picture table (nothing grants it any), so it reaches pictures only through its
    -- own doors; no policy names that role here, because it is made outside DMbot and may not
    -- exist where the migration runs.
    CREATE POLICY picture_sweep_read ON campaign_pictures FOR SELECT
        USING (current_setting('dmbot.picture_sweep', true) = 'keys');
    """
)

SQL = PICTURE_SWEEP_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("picture_actions",)
