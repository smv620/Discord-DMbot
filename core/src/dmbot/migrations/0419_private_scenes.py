"""0419_private_scenes

Private scenes between two player characters (#2996 rule 5; Supervisor's ruling, 2026-11-10): found
by code after a session, from the game clock and who spoke (`memory/private_scenes.py`).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- One row per private scene: a stretch of a session when the party was split and a group held
    -- exactly two player characters, played by two different people, who both spoke. **Ids and
    -- times only, never a word**: the two characters, the session's start, the first and last
    -- line's start (ms into the session's clock, as `transcript_lines.started_ms`), how many lines
    -- and who spoke them (`speaker_ids`, for the consent paths). Keyed by the session's start, not
    -- its id, so a backup carries it (a restore makes the session again under a new id).
    -- `status`: 'kept' is what the finder wrote, replaced whole each time it runs on the session;
    -- 'not_private' is the DM's "Not private", which a later run never undoes (the finder skips a
    -- scene of the same two that overlaps it). A kept row goes with the session's transcript and
    -- with anyone it cites; the DM's mark loses that person's id and stays.
    CREATE TABLE memory_private_scenes (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        first_id           TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, first_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        second_id          TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, second_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        session_started_at BIGINT NOT NULL CHECK (session_started_at > 0),
        from_ms            BIGINT NOT NULL CHECK (from_ms >= 0),
        to_ms              BIGINT NOT NULL,
        line_count         INTEGER NOT NULL CHECK (line_count >= 2),
        speaker_ids        BIGINT[] NOT NULL,
        status             TEXT NOT NULL CHECK (status IN ('kept', 'not_private')),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        decided_at         BIGINT,
        made_by_commit     TEXT CONSTRAINT memory_private_scenes_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (first_id < second_id),  -- (one order for a pair: sorted)
        CHECK (to_ms >= from_ms),
        -- (a kept row cites both its players, so the consent paths can always reach it)
        CHECK (status = 'not_private' OR cardinality(speaker_ids) >= 2),
        CHECK ((status = 'not_private') = (decided_at IS NOT NULL))
    );
    CREATE UNIQUE INDEX memory_private_scenes_once ON memory_private_scenes
        (guild_id, campaign_id, session_started_at, first_id, second_id, from_ms);
    CREATE TRIGGER memory_private_scenes_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_private_scenes
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();

    -- The reason PlotBot gives a rank it raised for a private scene between two player characters
    -- (#2996 rule 5: "always matters, always"). (`importance_rank.REASONS`; a test keeps the two
    -- equal.)
    ALTER TABLE memory_importance DROP CONSTRAINT memory_importance_reason_check;
    ALTER TABLE memory_importance ADD CONSTRAINT memory_importance_reason_check
        CHECK (reason IN ('reading', 'dm_corrected', 'revealed', 'demoted', 'rule', 'met_in_play',
                          'private_scene'));
    """ + _isolate("memory_private_scenes")

ISOLATED_TABLES: tuple[str, ...] = ("memory_private_scenes",)
