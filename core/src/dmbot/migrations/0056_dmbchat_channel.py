"""0056_dmbchat_channel."""

from __future__ import annotations

DMBCHAT_CHANNEL = """
    -- The table's DMbot chat channel (docs/PLAN.md, "Table questions: anyone may ask"; #1187):
    -- `dmb-chat-<slug>`, where anyone at the table asks DMbot a quick rules question and the
    -- answer is posted for everyone. Its id is a channel id: it means nothing in another
    -- server, so (like the DM screen's) none goes into a backup. A nullable column: no row
    -- changes, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN dmbchat_channel_id BIGINT;
"""

SQL = DMBCHAT_CHANNEL
