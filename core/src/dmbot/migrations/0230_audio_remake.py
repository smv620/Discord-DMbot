"""0230_audio_remake

What the audio did and how far (#2630).
"""

from __future__ import annotations

RECAP_AUDIO_REMAKE = """
    -- What the last making of a recap's audio did, and how far the one under way has got (#2630).
    -- `audio_how`: 'new' (made from nothing), 'voice' (only the changed paragraphs were spoken
    -- again, the music kept), 'all' (everything made again, `audio_why` says why: no_parts,
    -- paragraphs, moods or size) or 'same' (the spoken words had not changed). `audio_stage`,
    -- `audio_done` of `audio_total` and `audio_moved_at` are the live progress while the audio is
    -- queued: voice parts made, then music and mix. Codes and counts only; every column has a
    -- constant default, so no row changes.
    ALTER TABLE recap_jobs
        ADD COLUMN audio_how TEXT NOT NULL DEFAULT ''
            CHECK (audio_how IN ('', 'new', 'voice', 'all', 'same')),
        ADD COLUMN audio_why TEXT NOT NULL DEFAULT ''
            CHECK (audio_why IN ('', 'no_parts', 'paragraphs', 'moods', 'size')),
        ADD COLUMN audio_stage TEXT NOT NULL DEFAULT ''
            CHECK (audio_stage IN ('', 'voice', 'music', 'mix')),
        ADD COLUMN audio_done INT NOT NULL DEFAULT 0 CHECK (audio_done >= 0),
        ADD COLUMN audio_total INT NOT NULL DEFAULT 0 CHECK (audio_total >= 0),
        ADD COLUMN audio_moved_at BIGINT NOT NULL DEFAULT 0;
"""

SQL = RECAP_AUDIO_REMAKE
