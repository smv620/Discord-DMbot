"""0359_deed_proposals

Deeds the session reader proposed, waiting for the DM to record them (#3175).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

DEED_PROPOSALS = r"""
    -- A deed the story extractor thinks the party did (#3175 slice 4): who did it, what, whom it
    -- mattered to and how it landed, proposed for the DM to record with a button. It is a proposal
    -- only: nothing here is the story, and `add_deed` (DM only) is what records one. The words are
    -- the model's short telling and the names as said, DM-only: never a transcript line (only
    -- line NUMBERS are kept, as claims do), never on a player's screen. `speaker_ids` are the
    -- people whose lines it came from: a row goes when any of them stops being recorded.
    -- 'recorded' and 'dismissed' rows are kept so a pair is never asked about twice in a session.
    CREATE TABLE memory_deed_proposals (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        doer               TEXT NOT NULL CHECK (char_length(doer) BETWEEN 1 AND 100),
        text               TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 200),
        viewers            TEXT[] NOT NULL CHECK (cardinality(viewers) BETWEEN 1 AND 5),
        effect             TEXT NOT NULL CHECK (effect IN
                               ('Helped them', 'Pleased them', 'Annoyed them', 'Hurt them')),
        lines              TEXT[] NOT NULL,
        speaker_ids        BIGINT[] NOT NULL,
        session_started_at BIGINT,
        prompt_version     TEXT NOT NULL CHECK (char_length(prompt_version) BETWEEN 1 AND 40),
        model              TEXT NOT NULL CHECK (char_length(model) <= 80),
        status             TEXT NOT NULL CHECK (status IN ('proposed', 'recorded', 'dismissed')),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        decided_at         BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX memory_deed_proposals_by_status
        ON memory_deed_proposals (guild_id, campaign_id, status);
    """ + _isolate("memory_deed_proposals")

SQL = DEED_PROPOSALS

ISOLATED_TABLES: tuple[str, ...] = ("memory_deed_proposals",)
