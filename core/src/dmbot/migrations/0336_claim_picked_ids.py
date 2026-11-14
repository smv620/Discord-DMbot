"""0336_claim_picked_ids

A claim kept because a name in it could mean two entries remembers which one the DM picked (#3238).
"""

from __future__ import annotations

# "Which one?" (#3238): a claim about a player character whose other end could be two entries
# ("Mara" when there are two) is kept for the DM instead of being dropped. The DM's pick is
# recorded by the entry's id, not by rewriting the claim's words, because two entries can share the
# very same main name and no wording tells them apart. `subject_picked` and `object_picked` are
# NULL for every claim made before and for every claim nobody had to pick for; when set, the claim
# means that entry, ahead of what its words would find. Each is an entry id of the same campaign,
# kept as plain text like the claim's other pointers (`fact_id`), checked only for its shape. A new
# pair of columns on a table the website cannot read: no row is touched and no policy opened.
SQL = """
    ALTER TABLE memory_claims
        ADD COLUMN subject_picked TEXT
            CHECK (subject_picked IS NULL OR subject_picked ~ '^[0-9a-f]{32}$'),
        ADD COLUMN object_picked TEXT
            CHECK (object_picked IS NULL OR object_picked ~ '^[0-9a-f]{32}$');
"""
