"""0202_image_lines

A line that describes an image in the table chat (#2450).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

IMAGE_LINES = """
    -- A transcript line that describes an image posted in the table chat (#2450, docs/PLAN.md,
    -- "Images posted there"): stored as a typed line (everything that treats typed lines apart
    -- keeps doing so) and tagged "(image)" in the downloads. The image itself is never stored,
    -- only the words. Defaulted: no row changes.
    ALTER TABLE transcript_lines ADD COLUMN image BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_image_is_typed
        CHECK (NOT image OR typed);

    -- How many pictures a campaign has had described on a day (UTC day number), so the day's
    -- limit holds across restarts and across copies of the bot. A count only: no picture, no
    -- words, no one's name. Rows go with their campaign; older days are cleared as new ones are
    -- counted. New table: no rows change, so no backfill policy.
    CREATE TABLE image_day_use (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        day         INTEGER NOT NULL,
        used        INTEGER NOT NULL CHECK (used >= 0),
        PRIMARY KEY (guild_id, campaign_id, day),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """ + _isolate("image_day_use")

SQL = IMAGE_LINES

ISOLATED_TABLES: tuple[str, ...] = ("image_day_use",)
