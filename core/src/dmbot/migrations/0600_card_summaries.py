"""0600_card_summaries

A character's card says who they are in one sentence, where and when they were first met, and when
they were last mentioned (#5050; Supervisor's ruling on the issue, 2026-11-22).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- One row for each character, made and kept up by a background job, never when a card is
    -- opened. `who` is the one sentence the AI wrote from the entry's player-safe facts (the same
    -- parts `dmbot_entity_parts` gives, which leave out what the DM keeps secret and what the
    -- players don't know yet), so the same sentence is safe for the players' view later; it is NULL
    -- until the job has run. `facts_key` is a hash of the facts it was made from, so it is made
    -- again only when they change. First and last mention: the session (by its start, as
    -- `memory_name_sessions` keeps it), the line number in it (`Said.number`: the place among the
    -- session's stored lines) and DMbot's own short description of what was happening (a recap's
    -- scene, or one clause the AI made from the lines around it; never a quote of anyone's words,
    -- at most 300 characters). Counts, times, ids and DMbot's own words only: nothing a person
    -- said.
    -- It is made again from the facts and the transcripts, so it is not in a backup.
    CREATE TABLE memory_card_summaries (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id          TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        who                TEXT CHECK (who IS NULL OR char_length(who) BETWEEN 1 AND 600),
        facts_key          TEXT CHECK (facts_key IS NULL OR facts_key ~ '^[0-9a-f]{64}$'),
        made_at            BIGINT,
        first_session      BIGINT,
        first_line         INTEGER CHECK (first_line IS NULL OR first_line > 0),
        first_scene        TEXT CHECK (first_scene IS NULL OR char_length(first_scene) <= 300),
        last_session       BIGINT,
        last_line          INTEGER CHECK (last_line IS NULL OR last_line > 0),
        last_scene         TEXT CHECK (last_scene IS NULL OR char_length(last_scene) <= 300),
        scanned_at         BIGINT,
        made_by_commit     TEXT CONSTRAINT memory_card_summaries_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        CHECK ((first_session IS NULL) = (first_line IS NULL)),
        CHECK ((last_session IS NULL) = (last_line IS NULL))
    );
    CREATE TRIGGER memory_card_summaries_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_card_summaries
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_card_summaries")

ISOLATED_TABLES: tuple[str, ...] = ("memory_card_summaries",)
