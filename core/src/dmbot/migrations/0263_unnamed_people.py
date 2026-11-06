"""0263_unnamed_people

A person described but never named (#2790).
"""

from __future__ import annotations

# A person the story describes but never names (#2790): "Ken's sister", "a hooded figure". The row
# is a proposed character with no alias, so no name match can find it; `unnamed_what` is what the
# story calls them ("sister"), `unnamed_anchor` the entry they hang off (Ken; none for a hooded
# figure) and `unnamed_role` the relationship (sibling). The label is built from these when it is
# shown, so a rename or a merge of the anchor never leaves it stale. The key declares ON UPDATE
# CASCADE itself, because it carries guild_id (CLAUDE.md, #2550).
UNNAMED_PEOPLE = """
    ALTER TABLE memory_entities
        ADD COLUMN unnamed BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN unnamed_anchor TEXT,
        ADD COLUMN unnamed_role TEXT,
        ADD COLUMN unnamed_what TEXT;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_unnamed_shape CHECK (
        unnamed = (unnamed_what IS NOT NULL)
        AND (unnamed OR (unnamed_anchor IS NULL AND unnamed_role IS NULL))
        AND (unnamed_what IS NULL OR length(unnamed_what) BETWEEN 1 AND 100)
        AND (unnamed_role IS NULL OR length(unnamed_role) BETWEEN 1 AND 40)
        AND (unnamed_anchor IS NULL OR unnamed_anchor <> id));
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_unnamed_anchor_link
        FOREIGN KEY (guild_id, campaign_id, unnamed_anchor)
        REFERENCES memory_entities (guild_id, campaign_id, id)
        ON UPDATE CASCADE DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX memory_entities_by_unnamed_anchor
        ON memory_entities (guild_id, campaign_id, unnamed_anchor)
        WHERE unnamed_anchor IS NOT NULL;
    CREATE INDEX memory_entities_unnamed
        ON memory_entities (guild_id, campaign_id) WHERE unnamed;
"""

SQL = UNNAMED_PEOPLE
