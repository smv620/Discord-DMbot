"""0318_campaign_pictures

Pictures posted in the table's chat, kept with the campaign and shared by link (#3274).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

CAMPAIGN_PICTURES = """
    -- A picture someone who agreed under consent version 7 posted in the table's chat (#3274,
    -- docs/PLAN.md, "Pictures are kept, and shared by link"). The file itself is in the private
    -- bucket under `object_key` / `preview_key`; this row is everything else. `code` is the random
    -- link (`/pictures/<code>`, 32 random bytes, never guessable); the web door that resolves it
    -- without a sign-in is WebDev's. A picture posted twice in one campaign is one row (same
    -- `sha256`); files are never shared across campaigns. `public` is the default (anyone with the
    -- link); `permanent` exempts it from the rolling removal when the campaign is over its storage
    -- (at most 80% of the storage is permanent: the store checks that under a lock per campaign).
    -- `deleted_at` / `removed_for_room` keep the row so the link can say why it is gone, while the
    -- file and description are cleared. `poster_id` is who posted it; `right_granted_at` is when
    -- they agreed that their table may use it, under `consent_version`.
    CREATE TABLE campaign_pictures (
        id               BIGSERIAL PRIMARY KEY,
        guild_id         BIGINT NOT NULL,
        campaign_id      TEXT NOT NULL,
        poster_id        BIGINT NOT NULL CHECK (poster_id > 0),
        session_id       TEXT NOT NULL DEFAULT '',
        line_id          BIGINT,
        message_id       BIGINT NOT NULL DEFAULT 0,
        description      TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 2500),
        code             TEXT NOT NULL CHECK (char_length(code) >= 43),
        public           BOOLEAN NOT NULL DEFAULT TRUE,
        permanent        BOOLEAN NOT NULL DEFAULT FALSE,
        consent_version  INTEGER NOT NULL CHECK (consent_version >= 7),
        right_granted_at BIGINT NOT NULL CHECK (right_granted_at > 0),
        sha256           TEXT NOT NULL CHECK (char_length(sha256) = 64),
        object_key       TEXT NOT NULL CHECK (object_key <> ''),
        preview_key      TEXT NOT NULL DEFAULT '',
        size_bytes       BIGINT NOT NULL CHECK (size_bytes > 0),
        preview_bytes    BIGINT NOT NULL DEFAULT 0 CHECK (preview_bytes >= 0),
        made_at          BIGINT NOT NULL,
        deleted_at       BIGINT,
        removed_for_room BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (code),
        UNIQUE (campaign_id, guild_id, sha256),
        -- (a permanent picture that is gone is no longer permanent)
        CHECK (deleted_at IS NULL OR NOT permanent),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX campaign_pictures_campaign ON campaign_pictures (guild_id, campaign_id, id);
    CREATE INDEX campaign_pictures_person ON campaign_pictures (guild_id, poster_id);
    """ + _isolate("campaign_pictures")

SQL = CAMPAIGN_PICTURES

ISOLATED_TABLES: tuple[str, ...] = ("campaign_pictures",)
