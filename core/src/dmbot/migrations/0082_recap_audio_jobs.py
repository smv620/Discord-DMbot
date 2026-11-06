"""0082_recap_audio_jobs

The recap's audio tracks (#1383).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

RECAP_AUDIO_JOBS = (
    """
    -- The recap's two audio tracks (#1383), made after the text is ready; the text never waits
    -- for them. Per recap, kept on its row; the files themselves are on disk, not here.
    --   audio         off (not wanted, or no key), queued, ready or failed
    --   audio_tries   attempts so far (one retry later, then failed)
    --   audio_next_at not before this time (the wait before the retry)
    --   audio_credits the provider credits this making has cost, across its tries (a count);
    --                 a remake starts again at 0 (#2183)
    --   audio_error   a short fixed reason, never a name or any text
    ALTER TABLE recap_jobs ADD COLUMN audio TEXT NOT NULL DEFAULT 'off'
        CHECK (audio IN ('off', 'queued', 'ready', 'failed'));
    ALTER TABLE recap_jobs ADD COLUMN audio_tries INTEGER NOT NULL DEFAULT 0
        CHECK (audio_tries BETWEEN 0 AND 3);
    ALTER TABLE recap_jobs ADD COLUMN audio_next_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN audio_text_hash TEXT NOT NULL DEFAULT ''
        CHECK (char_length(audio_text_hash) <= 64);
    ALTER TABLE recap_jobs ADD COLUMN audio_credits INTEGER NOT NULL DEFAULT 0
        CHECK (audio_credits >= 0);
    ALTER TABLE recap_jobs ADD COLUMN audio_error TEXT NOT NULL DEFAULT ''
        CHECK (char_length(audio_error) <= 200);
    --   audio_text_hash  a fingerprint of the title and text the audio was made from: after the
    --                 DM edits the text the audio no longer matches and is not attached
    -- A campaign's pronunciation dictionary at the voice company, made once and kept here with
    -- a hash of its rules, so it is made again only when a name's spelling changes. Only
    -- non-secret names are ever in it. It belongs to this campaign alone and goes with it.
    CREATE TABLE recap_dictionaries (
        """
    + "{scope}"
    + """
        dictionary_id TEXT NOT NULL CHECK (char_length(dictionary_id) BETWEEN 1 AND 100),
        version_id    TEXT NOT NULL CHECK (char_length(version_id) BETWEEN 1 AND 100),
        rules_hash    TEXT NOT NULL CHECK (char_length(rules_hash) = 64),
        updated_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """
    + _isolate("recap_dictionaries")
)

RECAP_AUDIO_JOBS = RECAP_AUDIO_JOBS.replace("{scope}", _memory_scope())

SQL = RECAP_AUDIO_JOBS

ISOLATED_TABLES: tuple[str, ...] = ("recap_dictionaries",)
