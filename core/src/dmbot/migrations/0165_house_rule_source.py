"""0165_house_rule_source

Where each house rule came from (#2285).
"""

from __future__ import annotations

HOUSE_RULE_SOURCE = """
    -- Where each house rule came from (#2285; docs/PLAN.md, "Seeing where house rules come from"):
    -- 'discord' is every rule from before this was recorded ("added in Discord"), 'typed' the
    -- Add form, 'voice' a rule said at the table, 'alert' one made from a rules card, 'linked' one
    -- accepted from the linked file, 'upload' one accepted from a file the DM uploaded (whose name
    -- is `source_name`). New columns with defaults: no row is rewritten, so no row-level security
    -- to open.
    ALTER TABLE house_rules
        ADD COLUMN source TEXT NOT NULL DEFAULT 'discord'
            CHECK (source IN ('discord', 'typed', 'voice', 'alert', 'linked', 'upload')),
        ADD COLUMN source_name TEXT
            CHECK (source_name IS NULL OR char_length(source_name) BETWEEN 1 AND 200);
"""

SQL = HOUSE_RULE_SOURCE
