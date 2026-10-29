"""0007_transcript_channel."""

from __future__ import annotations

TRANSCRIPT_CHANNEL = """
    -- The campaign's live transcript channel, dmb-transcript-<short name> (#124). Like
    -- the DM screen it belongs to the server, so it isn't part of backups.
    ALTER TABLE campaigns ADD COLUMN transcript_channel_id BIGINT;
    """

SQL = TRANSCRIPT_CHANNEL
