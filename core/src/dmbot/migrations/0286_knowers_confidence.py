"""0286_knowers_confidence

Each point of view's certainty in a statement (#2995; STORY_MEMORY §9b item 2).
"""

from __future__ import annotations

KNOWERS_CONFIDENCE = r"""
    -- "Everything gets a certainty from each perspective" (owner, 2026-10-29): a row in
    -- memory_knowers now says how sure that character is, from 0.0 (knows of it and believes it
    -- false) to 1.0. No row still means the character does not know of it at all. Every row there
    -- is has been written as plain knowledge, so it is 1.0; no row changes.
    ALTER TABLE memory_knowers
        ADD COLUMN confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
            CHECK (confidence >= 0 AND confidence <= 1);
    """

SQL = KNOWERS_CONFIDENCE
