"""0578_relation_hand_over

A kept hand-over says who the item came from (#4170; Supervisor's ruling, 2026-11-21), so a tie of
theirs kept after it is stored already ended.
"""

from __future__ import annotations

# "Claude sold the figurine to Belleros": keeping the sale ends Claude's live `owns` and `carries`
# of it (#4115, `backstory_keep.hand_over_ties`). When the same upload's "Claude carries the
# figurine" was still waiting and is kept AFTER the sale, nothing said the item had moved on, so it
# was kept as current, beside Belleros's. The kept row now carries the waiting row's two columns
# (0423, the same checks), and a seller's tie kept later is kept ended, with the sale's reason; an
# Undo of the sale opens it again (`store._reopen_after_hand_over_undo`).
# No backfill (Supervisor's condition): a row kept before this has '' in both, as every connection
# that is no hand-over does, so an older sale ends nothing kept after this; its own ends stand. No
# foreign key, as on the waiting row: a merge moves the id to the entry kept (`_merge_in`), and an
# id a delete left behind matches no tie, so the connection stays a plain one. No row changes, so no
# row-level security needs opening.
SQL = r"""
    ALTER TABLE memory_relations
        ADD COLUMN hand_over_from TEXT NOT NULL DEFAULT ''
            CHECK (hand_over_from = '' OR hand_over_from ~ '^[0-9a-f]{32}$'),
        ADD COLUMN hand_over TEXT NOT NULL DEFAULT ''
            CHECK (hand_over IN ('', 'sold', 'gave', 'traded', 'stolen', 'lost')),
        ADD CONSTRAINT memory_relations_hand_over_whole
            CHECK ((hand_over = '') = (hand_over_from = '')),
        ADD CONSTRAINT memory_relations_hand_over_kind
            CHECK (hand_over = '' OR predicate IN ('owns', 'carries'));
    """
