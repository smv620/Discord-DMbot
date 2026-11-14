"""0414_memory_met

The beings the party met in play that the story never named (#3172 slice 5; Supervisor's ruling,
option C, 2026-11-10): kept apart from the campaign's names until the DM names or joins one.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- One row per encounter in a session (two ambushers, the sled dogs, a monstrosity the DM says
    -- the party senses), as the session reader found it in the DM's own lines. It is NOT an entry:
    -- nothing that lists names reads this table, so it is quiet by construction (the name check,
    -- the waiting counts and every player view never see it). Only the DM's list of them (#4045)
    -- will read it; naming or joining one there writes the entry at rank 0 (reason `met_in_play`)
    -- and deletes this row in one transaction. `what` is the DM's word for them and `line_ms`
    -- the start of each line it came from (with the session, a line's own key: a line number
    -- would shift when someone else's lines are deleted), so a row is transcript-derived: it goes
    -- with its session (the key below), with the session's transcript
    -- (`delete_session_transcript`) and with anyone it cites (`speaker_ids`: "Stop recording me",
    -- "Delete my transcripts", a DM's removal).
    -- `count` 1 is one being; 0 is "several". At most 10 rows a session (`met_store.add`).
    CREATE TABLE memory_met (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        session_id         TEXT NOT NULL,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        session_started_at BIGINT,
        what               TEXT NOT NULL CHECK (char_length(what) BETWEEN 1 AND 100),
        what_key           TEXT NOT NULL CHECK (char_length(what_key) BETWEEN 1 AND 200),
        count              INTEGER NOT NULL CHECK (count BETWEEN 0 AND 999),
        line_ms            BIGINT[] NOT NULL CHECK (cardinality(line_ms) BETWEEN 1 AND 10),
        speaker_ids        BIGINT[] NOT NULL CHECK (cardinality(speaker_ids) >= 1),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit     TEXT CONSTRAINT memory_met_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    -- One row for the same words in a session, even if two reads overlap (a restarted read).
    CREATE UNIQUE INDEX memory_met_once ON memory_met (guild_id, campaign_id, session_id, what_key);
    CREATE INDEX memory_met_by_session ON memory_met (guild_id, session_id);
    CREATE TRIGGER memory_met_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_met
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();

    -- The reason a name the DM gives one of them (#4045) is ranked with: rank 0, `met_in_play`.
    -- (The CHECK names the reasons `importance_rank.REASONS` lists; a test keeps the two equal.)
    ALTER TABLE memory_importance DROP CONSTRAINT memory_importance_reason_check;
    ALTER TABLE memory_importance ADD CONSTRAINT memory_importance_reason_check
        CHECK (reason IN ('reading', 'dm_corrected', 'revealed', 'demoted', 'rule', 'met_in_play'));
    """ + _isolate("memory_met")

ISOLATED_TABLES: tuple[str, ...] = ("memory_met",)
