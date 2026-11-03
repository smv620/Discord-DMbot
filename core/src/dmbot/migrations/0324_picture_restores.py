"""0324_picture_restores

Restoring a backup's pictures: a "waiting for its file" mark on a picture, and the website's queue
of picture files to put back (#3274).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

PICTURE_RESTORES = r"""
    -- A picture restored from a backup whose file has not come back yet (a Discord restore takes
    -- at most 24 files, so a big campaign's pictures can be longer than that). Its row is there so
    -- its link, poster and permanent mark survive, and the link page says "This picture is being
    -- restored." until the file arrives; it has no files, so it uses no storage and the rolling
    -- removal never touches it (the store counts and removes only 'ready' rows).
    ALTER TABLE campaign_pictures
        ADD COLUMN restore_state TEXT NOT NULL DEFAULT 'ready'
            CHECK (restore_state IN ('ready', 'waiting'));
    ALTER TABLE campaign_pictures DROP CONSTRAINT campaign_pictures_object_key_check;
    ALTER TABLE campaign_pictures ADD CONSTRAINT campaign_pictures_files_check
        CHECK (restore_state = 'waiting' OR object_key <> '');

    -- One picture-files part the DM gave the website (#3274), waiting for the bot to read it and
    -- put its pictures back. The website's role has no right on the picture tables, so its door
    -- only checks the DM and queues the part here; the bot reads each picture again through
    -- `prepare` (so nothing in the file is trusted), uploads it, and fills in the waiting rows it
    -- matches. Taken in the order made (`seq`). `part` is the file as uploaded (at most 40 MB) and
    -- is cleared the moment it is applied; `note` is what the page shows afterwards, counts only.
    -- A row lives an hour at most, is isolated per server, left out of backups (it is a request,
    -- not content) and goes with its campaign.
    CREATE TABLE picture_restores (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        part        BYTEA,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'refused', 'failed')),
        restored    INTEGER NOT NULL DEFAULT 0 CHECK (restored >= 0),
        left_out    INTEGER NOT NULL DEFAULT 0 CHECK (left_out >= 0),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX picture_restores_open ON picture_restores (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """ + _isolate("picture_restores")

SQL = PICTURE_RESTORES

ISOLATED_TABLES: tuple[str, ...] = ("picture_restores",)
