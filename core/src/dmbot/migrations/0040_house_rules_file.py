"""0040_house_rules_file."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

HOUSE_RULES_FILE = f"""
    -- A campaign's linked house-rules file (#969, migration 0040): the share link a DM set, and the
    -- fingerprint of the file they chose to "ignore until it changes". One row per campaign,
    -- deleted with it. The link can be a private share link, so it is never logged, never
    -- shown back in full (only the site it is on), and not part of a backup. Only the
    -- campaign's DMs set or clear it (dmbot.rules.house_file_link checks that in the same
    -- transaction). The website's role has no grant on this table.
    CREATE TABLE house_rules_file (
        {_memory_scope()}
        link    TEXT NOT NULL CHECK (char_length(link) BETWEEN 1 AND 2000),
        ignored TEXT CHECK (ignored IS NULL OR char_length(ignored) = 64),
        set_by  BIGINT NOT NULL CHECK (set_by > 0),
        set_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """ + _isolate("house_rules_file")

SQL = HOUSE_RULES_FILE

ISOLATED_TABLES: tuple[str, ...] = ("house_rules_file",)
