"""0297_belief_lineage

Why a belief changed (#3091, part 1): the earlier belief a new one revises, and what kind of change
it was; and a `source` evidence kind pointing at the entry that said something.
"""

from __future__ import annotations

BELIEF_LINEAGE = """
    -- `derived_from` is the earlier belief this one revises or comes from (the same point of
    -- view for an update, a retraction or a branch; another point of view's for "told by");
    -- `change_kind` says which. Only the newest belief of a (point of view, connection) pair is
    -- current, as before: this column only records where a belief came from. Deleting the
    -- earlier belief (its connection was deleted) leaves the link as a PLACEHOLDER:
    -- `derived_from` is cleared, `change_kind` stays, so "revised something no longer
    -- available" can still be said, and the belief never carries the words of what it came from.
    -- The foreign key follows a campaign that moves to another server (ON UPDATE CASCADE).
    ALTER TABLE memory_beliefs
        ADD COLUMN derived_from TEXT,
        ADD COLUMN change_kind TEXT
            CHECK (change_kind IN ('update', 'branch', 'retraction', 'told')),
        ADD CONSTRAINT memory_beliefs_lineage_kind
            CHECK (derived_from IS NULL OR change_kind IS NOT NULL),
        ADD CONSTRAINT memory_beliefs_derived_from_fk
            FOREIGN KEY (guild_id, campaign_id, derived_from)
            REFERENCES memory_beliefs (guild_id, campaign_id, id)
            ON DELETE SET NULL (derived_from) ON UPDATE CASCADE
            DEFERRABLE INITIALLY DEFERRED;  -- (a restore may bring the later belief first)
    CREATE INDEX memory_beliefs_by_derived_from
        ON memory_beliefs (guild_id, campaign_id, derived_from) WHERE derived_from IS NOT NULL;

    -- A new kind of evidence: `source`, the id of the entry (a character, a person) that said it.
    -- Like a clue or an event it points at a row of the same campaign, never at words.
    ALTER TABLE memory_evidence DROP CONSTRAINT memory_evidence_kind_check;
    ALTER TABLE memory_evidence ADD CONSTRAINT memory_evidence_kind_check
        CHECK (kind IN ('claim', 'line', 'clue', 'event', 'source'));
"""

SQL = BELIEF_LINEAGE

ISOLATED_TABLES: tuple[str, ...] = ()
