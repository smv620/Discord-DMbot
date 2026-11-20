"""0549_party_spans

The party record (#4537, party knowledge slice 2a; `docs/design/party_knowledge.md` §3, Supervisor's
ruling on #4537, 2026-11-18): who was in the party when.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one stretch of one player character in the party.
# - `from_at`: the start of the session it begins at (a session running when the character joined)
#   or, between sessions, the moment it began; `to_at` (with `ended`, one word: left, retired, died,
#   away) is where it ended, NULL while the character is in the party. `from_minute`/`to_minute`
#   are the game minute at each end, only when the campaign keeps a game clock.
# - `source`: `dm` (the DM's own action) or `start` (written here, for an older campaign).
# - A character has at most one open row (the unique index); stretches never overlap, which the
#   store checks under its write lock (an overlap constraint needs an extension the schema lacks).
# - It goes with its character (`ON DELETE CASCADE`: an erased or merged-away character takes its
#   rows, §3) and follows a campaign moved to another server (`ON UPDATE CASCADE`, #2550).
# - Ids and times only, never a word of the story.
#
# **The backfill** (§3 "Older campaigns"; Supervisor's ruling): each confirmed player character gets
# one open row, `source = 'start'`, from when it last BECAME one: the latest logged change of its
# entry whose row after is a confirmed player character and whose row before was not (or, with no
# such change, the entry's own `created_at`). That moment is moved back to the start of the session
# running then, if any; with no session, it stays as it is. Nobody is treated as having left.
# Migrations run with no server set, so forced row-level security hides every row: each table the
# statement reads or writes is opened for it alone and closed again in the same transaction
# (CLAUDE.md; the lessons of #704 and #781).
SQL = (
    r"""
    CREATE TABLE memory_party_spans (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        entity_id       TEXT NOT NULL,
        from_at         BIGINT NOT NULL CHECK (from_at >= 0),
        to_at           BIGINT,
        from_minute     BIGINT,
        to_minute       BIGINT,
        ended           TEXT CHECK (ended IN ('left', 'retired', 'died', 'away')),
        source          TEXT NOT NULL CHECK (source IN ('dm', 'start')),
        created_at      BIGINT NOT NULL,
        made_by_commit  TEXT CONSTRAINT memory_party_spans_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((to_at IS NULL) = (ended IS NULL)),
        CHECK (to_at IS NULL OR to_at >= from_at),
        CHECK (to_minute IS NULL OR to_at IS NOT NULL),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX memory_party_spans_one_open
        ON memory_party_spans (guild_id, campaign_id, entity_id) WHERE to_at IS NULL;
    CREATE INDEX memory_party_spans_by_entity
        ON memory_party_spans (guild_id, campaign_id, entity_id, from_at);
    CREATE TRIGGER memory_party_spans_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_party_spans
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """
    + _isolate("memory_party_spans")
    + r"""
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON transcript_sessions FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_party_spans FOR ALL USING (true) WITH CHECK (true);
    INSERT INTO memory_party_spans
        (guild_id, campaign_id, id, entity_id, from_at, source, created_at)
    SELECT e.guild_id, e.campaign_id, md5(e.guild_id::TEXT || ':' || e.campaign_id || ':' || e.id),
           e.id,
           COALESCE(
               (SELECT max(s.started_at) FROM transcript_sessions s
                 WHERE s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id
                   AND s.started_at <= became.at
                   AND (s.ended_at IS NULL OR s.ended_at >= became.at)),
               became.at),
           'start', extract(epoch FROM now())::BIGINT
    FROM memory_entities e
    CROSS JOIN LATERAL (
        SELECT COALESCE(
            (SELECT max(c.made_at) FROM memory_changes c
              WHERE c.guild_id = e.guild_id AND c.campaign_id = e.campaign_id
                AND c.table_name = 'memory_entities' AND c.row_id = e.id
                AND c.after ->> 'role' = 'player_character'
                AND c.after ->> 'status' = 'confirmed'
                AND (c.before IS NULL
                     OR c.before ->> 'role' IS DISTINCT FROM 'player_character'
                     OR c.before ->> 'status' IS DISTINCT FROM 'confirmed')),
            e.created_at) AS at
    ) AS became
    WHERE e.type = 'character' AND e.role = 'player_character' AND e.status = 'confirmed';
    DROP POLICY migrate_backfill ON memory_party_spans;
    DROP POLICY migrate_backfill ON transcript_sessions;
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_entities;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("memory_party_spans",)
