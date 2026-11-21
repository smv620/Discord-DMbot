"""0579_attitude_targets

A group's or place's feeling toward a lineage or a named culture, and DMbot's own reads of one
(#4229 slice 4; Supervisor's ruling on #4229, 2026-11-21).
"""

from __future__ import annotations

# "Mithral Hall distrusts orcs": `memory_attitudes` (0117) held only an attitude toward an entry
# (a `people` the story treats as an actor), set by the DM. The owner's ruling of 2026-11-12 keeps
# lineages and cultures off the graph (never a node), so an attitude may now point at one of three:
# - `toward_id`, an entry, as before;
# - `toward_lineage`, a lineage key of `memory/place_peoples.LINEAGES` (the store checks the key
#   against that list when it writes, Supervisor's condition; the CHECK only keeps its shape);
# - `toward_culture_id`, a named culture of the campaign (0496), deleted with it.
# Exactly one is set. `status`: 'confirmed' (the DM's, every row before this) or 'read' (DMbot
# read it in the lore; it waits in World notes until the DM keeps it, and counts for nothing until
# then). The one-open rule now takes whichever target is set. No row changes (the new columns are
# empty or take their default), so no row-level security needs opening.
SQL = r"""
    ALTER TABLE memory_attitudes
        ALTER COLUMN toward_id DROP NOT NULL,
        ADD COLUMN toward_lineage TEXT CHECK (toward_lineage ~ '^[a-z]{1,20}$'),
        ADD COLUMN toward_culture_id TEXT,
        ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed'
            CHECK (status IN ('read', 'confirmed')),
        ADD CONSTRAINT memory_attitudes_one_target CHECK (
            (toward_id IS NOT NULL)::int + (toward_lineage IS NOT NULL)::int
            + (toward_culture_id IS NOT NULL)::int = 1
        ),
        ADD CONSTRAINT memory_attitudes_culture FOREIGN KEY (guild_id, campaign_id,
            toward_culture_id) REFERENCES memory_cultures (guild_id, campaign_id, id)
            ON UPDATE CASCADE ON DELETE CASCADE;
    DROP INDEX memory_attitudes_one_open;
    CREATE UNIQUE INDEX memory_attitudes_one_open
        ON memory_attitudes (guild_id, campaign_id, holder_id, COALESCE(toward_id, ''),
                             COALESCE(toward_lineage, ''), COALESCE(toward_culture_id, ''))
        WHERE closed_at IS NULL;
    -- (deleting a culture cascades here: without an index each delete scans the table)
    CREATE INDEX memory_attitudes_toward_culture
        ON memory_attitudes (guild_id, campaign_id, toward_culture_id)
        WHERE toward_culture_id IS NOT NULL;
    """
