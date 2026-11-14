"""0423_hand_over_claims

A lore connection waiting for the DM that says an item changed hands (#4115 slice 2; Supervisor's
ruling, 2026-11-10): who had it before and how it moved.
"""

from __future__ import annotations

SQL = r"""
    -- "Belleros owns the Frost Brand (Claude sold it)": the waiting connection is the new holder's,
    -- and these say who had it before (`hand_over_from`, an entry id) and how it moved
    -- (`hand_over`: sold, gave, traded, stolen or lost). Both '' for every other connection, and
    -- for every row from before this. Keeping it ends what the one before held, as history, with
    -- the reason (`memory_relations.end_reason`, 0422); turning it down changes nothing. No
    -- foreign key: the id is followed through merges when the queue is read, as its two ends are,
    -- and one that is gone leaves a plain connection. No existing row changes (the defaults are
    -- empty), so no row-level security needs opening.
    ALTER TABLE backstory_links
        ADD COLUMN hand_over_from TEXT NOT NULL DEFAULT ''
            CHECK (hand_over_from = '' OR hand_over_from ~ '^[0-9a-f]{32}$'),
        ADD COLUMN hand_over TEXT NOT NULL DEFAULT ''
            CHECK (hand_over IN ('', 'sold', 'gave', 'traded', 'stolen', 'lost')),
        ADD CONSTRAINT backstory_links_hand_over_whole
            CHECK ((hand_over = '') = (hand_over_from = '')),
        ADD CONSTRAINT backstory_links_hand_over_kind
            CHECK (hand_over = '' OR predicate IN ('owns', 'carries'));
    """
