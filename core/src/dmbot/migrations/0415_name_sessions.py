"""0415_name_sessions

Which session the story reader made a waiting name in (#4043; Supervisor's review of #4044).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- The session a name was made in, by its start (unique in a campaign, an imported session's
    -- DM-chosen date included). The split-party check (`store.name_scene`) reads it to know which
    -- session's lines to look at; before this, it guessed the newest session that began before the
    -- name was made, which picks the wrong one when an import is dated before a live session.
    -- A time and an id only, never a word. A name made before this table falls back to the guess.
    CREATE TABLE memory_name_sessions (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id          TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        session_started_at BIGINT NOT NULL,
        made_by_commit     TEXT CONSTRAINT memory_name_sessions_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, entity_id)
    );
    CREATE TRIGGER memory_name_sessions_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_name_sessions
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_name_sessions")

ISOLATED_TABLES: tuple[str, ...] = ("memory_name_sessions",)
