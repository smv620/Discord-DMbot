"""0120_dm_bot_channel

The DM Bot channel and its panel (#1666).
"""

from __future__ import annotations

DM_BOT_CHANNEL = """
    -- The DM Bot channel (docs/PLAN.md, "The DM Bot channel"; #1666): `dmb-dm-bot-<slug>`,
    -- everyone reads, only DMbot posts, with one panel message of buttons by role. Its ids are
    -- a channel id and a message id: they mean nothing in another server, so (like the DM
    -- screen's) none goes into a backup. Nullable columns: no row changes, so no row-level-
    -- security policy is needed.
    ALTER TABLE campaigns ADD COLUMN dm_bot_channel_id BIGINT;
    ALTER TABLE campaigns ADD COLUMN dm_bot_message_id BIGINT;
"""

SQL = DM_BOT_CHANNEL
