"""0031_house_rules."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

HOUSE_RULES = f"""
    -- A campaign's house rules (#865; docs/PLAN.md, "House rules"): the rule, the book rule
    -- it replaces (free text), and what happened to make it (free text, optional). Per
    -- campaign, never shared between campaigns or servers; deleted with the campaign; in
    -- its backups. Only the campaign's DMs change them (dmbot.rules.house checks that in
    -- the same transaction); anyone in the server may list them. The website's role has no
    -- grant on this table.
    --   number      the rule's own number, for good: the campaign's next one when it is
    --               made (campaigns.house_rules_made counts them, so a number is never
    --               used twice, even after a rule is removed). Alerts cite "house rule 12".
    --   version     counts changes to the rule, so an edit or a removal made from an old
    --               view can be told from one made on the current rule
    --   session_id  the stored transcript session it came from, for the later parts (an
    --               alert's Override, a rule declared aloud); nothing sets it yet, and a
    --               backup never carries it (it means nothing in another server)
    ALTER TABLE campaigns ADD COLUMN house_rules_made INTEGER NOT NULL DEFAULT 0
        CHECK (house_rules_made >= 0);
    CREATE TABLE house_rules (
        {_memory_scope()}
        number     INTEGER NOT NULL CHECK (number > 0),
        rule       TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        supersedes TEXT CHECK (supersedes IS NULL OR char_length(supersedes) BETWEEN 1 AND 500),
        scenario   TEXT CHECK (scenario IS NULL OR char_length(scenario) BETWEEN 1 AND 500),
        session_id TEXT,
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        version    INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        PRIMARY KEY (guild_id, campaign_id, number)
    );
    """ + _isolate("house_rules")

SQL = HOUSE_RULES

ISOLATED_TABLES: tuple[str, ...] = ("house_rules",)
