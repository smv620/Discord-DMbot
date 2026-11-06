"""0236_claim_readings

A claim can be a reading, a belief to place (#2412).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_WAITING,
)

_WAITING_DOOR = WEB_WAITING[WEB_WAITING.index("CREATE OR REPLACE FUNCTION dmbot_web_waiting(") :]

CLAIM_READINGS = """
    -- A claim can be a reading (#2412, docs/STORY_MEMORY.md 9 and 9a): not a fact someone said
    -- but a belief for the DM to place, such as a theory, a reveal, a frame-up or what a spell
    -- showed. `reading` holds what the reading adds to the claim's words: the point of view
    -- that holds it, its confidence and status (set by code, never by the model), the clue,
    -- event or spell that justifies it, the act that faked it and whether the DM alone may
    -- write it. Names, numbers and words of the game only, never a Discord id or a quote; a
    -- claim's lines and speakers are the claim's own and go with a person's words as before.
    -- NULL for every claim made before. A new column on a table the website cannot read, so no
    -- row is touched and no policy opened.
    ALTER TABLE memory_claims ADD COLUMN reading JSONB
        CHECK (reading IS NULL OR (jsonb_typeof(reading) = 'object'
                                   AND pg_column_size(reading) <= 2000));
    -- A reading is a belief to place, not a fact to say yes to: the website's list of what waits
    -- for the DM leaves it out (its claims and its count), as the Discord list does. Cut from
    -- the door as 0233 made it, with that one condition added.
""" + _WAITING_DOOR.replace(
    "AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0",
    "AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0 AND k.reading IS NULL",
)

if _WAITING_DOOR.count("AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0") != 2:
    # (an explicit raise: `python -O` drops an assert, and this door must not change silently)
    raise RuntimeError("the waiting door no longer has the two filters 0236 widens")

SQL = CLAIM_READINGS
