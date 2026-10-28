"""0128_npcs_channel

The NPC roster channel (#1761).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

NPCS_CHANNEL = f"""
    -- The NPC roster channel (docs/PLAN.md, "Channels"; #1761): `dmb-npcs-<slug>`, a controlled
    -- channel that follows the DM screen's visibility and lists the confirmed NPCs and groups the
    -- players may know, read only. Like the other channel ids it means nothing in another server,
    -- so none goes into a backup. A nullable column: no row changes, so no row-level-security
    -- policy.
    ALTER TABLE campaigns ADD COLUMN npcs_channel_id BIGINT;

    -- Which Discord message shows which entry, so the channel's text never carries an id. Message
    -- ids mean nothing in another server, so it is not in a backup either; a lost row is made
    -- good at the next start (DMbot's own messages with no row are deleted and posted again).
    -- `entry_id` is an entry's id, or 'more' for the "+N more" message. New table, no rows.
    CREATE TABLE npcs_channel_messages (
        {_memory_scope()}
        entry_id    TEXT NOT NULL CHECK (length(entry_id) BETWEEN 1 AND 32),
        message_id  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entry_id)
    );
    """ + _isolate("npcs_channel_messages")

SQL = NPCS_CHANNEL

ISOLATED_TABLES: tuple[str, ...] = ("npcs_channel_messages",)
