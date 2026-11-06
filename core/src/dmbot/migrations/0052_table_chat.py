"""0052_table_chat."""

from __future__ import annotations

TABLE_CHAT = """
    -- Typed chat at the table (#1118; docs/PLAN.md, "Typed chat at the table"). The voice
    -- channel's own text chat is read while DMbot listens (and only from people who agreed);
    -- `table_chat_channel_id` is at most one more text channel the DM picked, and
    -- `table_chat` is the DM's switch (on for everyone, because the consent wording that
    -- covers it was asked again, version 5). A typed line is stored with `typed`, shown only
    -- in the as-heard transcript and tagged "(typed)". Nullable or defaulted columns: no row
    -- changes, so no backfill policy.
    ALTER TABLE campaigns
        ADD COLUMN table_chat BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN table_chat_channel_id BIGINT;
    ALTER TABLE transcript_lines ADD COLUMN typed BOOLEAN NOT NULL DEFAULT FALSE;
    -- A DM sidebar question typed in the table's chat has its own "via" (no speech-to-text).
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_via_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_via_check
        CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger', 'table-chat'));
"""

SQL = TABLE_CHAT
