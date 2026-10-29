"""0106_waiting_name_questions

Unanswered "Did they mean…?" (#1572).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WAITING_NAME_QUESTIONS = """
    -- "Did they mean…?" questions nobody answered during a session (#1572; docs/PLAN.md,
    -- "Transcript Cleaner"): kept for the after-session check, so the DM can still say that
    -- "Marin" was Maren. The line is named like transcript_suggestions names one (`session_id`,
    -- `speaker_id`, `started_ms`): its words are read from the line when the DM answers, so
    -- nothing of the line is copied here. `heard` is the misheard word and `option_ids` the
    -- 2 or 3 names it might be (entry IDs, read again when asked, since a name can be removed
    -- or made secret meanwhile). One row for a word in a line; open ones expire after 7 days
    -- (state `expired`, no message). A new table, so no existing row changes and no row-level
    -- security needs opening.
    CREATE TABLE waiting_name_questions (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        speaker_id  BIGINT NOT NULL CHECK (speaker_id > 0),
        started_ms  BIGINT NOT NULL,
        heard       TEXT NOT NULL CHECK (char_length(heard) BETWEEN 1 AND 100),
        option_ids  TEXT[] NOT NULL CHECK (cardinality(option_ids) BETWEEN 2 AND 3),
        state       TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'answered', 'kept', 'expired')),
        created_at  BIGINT NOT NULL,
        decided_at  BIGINT,
        decided_by  BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX waiting_name_questions_one_per_word
        ON waiting_name_questions (session_id, speaker_id, started_ms, heard);
    CREATE INDEX waiting_name_questions_open
        ON waiting_name_questions (campaign_id, state, created_at);
    """ + _isolate("waiting_name_questions")

SQL = WAITING_NAME_QUESTIONS

ISOLATED_TABLES: tuple[str, ...] = ("waiting_name_questions",)
