"""0127_rules_channel

The rules archive channel (#1752).
"""

from __future__ import annotations

RULES_CHANNEL = """
    -- The rules archive channel (docs/PLAN.md, "Channels"; #1752): `dmb-rules-<slug>`, a
    -- controlled channel that follows the DM screen's visibility and shows the campaign's house
    -- rules, read only. Like the other channel ids it means nothing in another server, so none
    -- goes into a backup. A nullable column: no row changes, so no row-level-security policy.
    ALTER TABLE campaigns ADD COLUMN rules_channel_id BIGINT;
"""

SQL = RULES_CHANNEL
