"""0636_graph_review_posting

Where a graph improvement report stands on its way to the private repository (#3742, slice B).
"""

from __future__ import annotations

# `graph_reviews` (0386) holds each careful-check report; `verdicts` (0635) is the structure it is
# posted from. These columns are the poster's own bookkeeping, all small numbers and words, never a
# word of a report: `posting_at` is when the poster last took the row (its lease: a row it holds is
# not taken again for ten minutes, and after a failure or a hold not for an hour), `discussion` is
# the number of the Discussion it became (set with `posted_at`, in the same UPDATE), `post_failures`
# counts the tries that failed (five, and the poster stops trying that report) and `held` says why a
# report is held back in a fixed word ("speaker_stopped", "not_sharing"), empty when it is not.
# No rows change, so no row-level-security backfill is needed.
SQL = r"""
    ALTER TABLE graph_reviews
        ADD COLUMN posting_at BIGINT,
        ADD COLUMN discussion INTEGER CHECK (discussion IS NULL OR discussion > 0),
        ADD COLUMN post_failures INTEGER NOT NULL DEFAULT 0
            CHECK (post_failures BETWEEN 0 AND 10),
        ADD COLUMN held TEXT NOT NULL DEFAULT '' CHECK (held ~ '^[a-z_]{0,30}$'),
        ADD CONSTRAINT graph_reviews_discussion_posted
            CHECK (discussion IS NULL OR posted_at IS NOT NULL);
    CREATE INDEX graph_reviews_unposted ON graph_reviews (guild_id, created_at)
        WHERE posted_at IS NULL;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
