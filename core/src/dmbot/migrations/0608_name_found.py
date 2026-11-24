"""0608_name_found

What made a suggested name a name (#5133 slice 3b; docs/design/session_names.md, section 6, as
Supervisor ruled with the Expert's check): how the after-session step found it, and a pointer to the
one line that made it a name, so the card can quote that line and order its list by how sure DMbot
is.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- One row for each waiting name the after-session name step suggested. `how` is how it was
    -- found: introduced by name, typed by a person, addressed, a place, spoken of, or by the scan
    -- alone (the card puts introduced and typed first). `line_ref` points at the one line that made
    -- it a name, as `memory_mentions` points at a line (the session, the line's start and its
    -- speaker), never the words: the card reads the line when it draws, with who agrees to be
    -- recorded asked then, and leaves it out when they no longer do. NULL when no line is kept.
    -- It goes when the name is answered (kept, refused, joined), when its entry goes, and when its
    -- line goes (a speaker's lines deleted, or the transcript), beside the mentions. It is about a
    -- waiting suggestion only, so it is not in a backup: after a restore the card has no quote.
    CREATE TABLE name_found (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id    TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        how          TEXT NOT NULL
            CHECK (how IN ('introduced', 'typed', 'addressed', 'place', 'spoken_of', 'scan')),
        line_ref     TEXT CHECK (line_ref IS NULL OR length(line_ref) <= 100),
        found_at     BIGINT NOT NULL CHECK (found_at > 0),
        PRIMARY KEY (guild_id, campaign_id, entity_id)
    );
    CREATE INDEX name_found_by_line ON name_found (guild_id, line_ref) WHERE line_ref IS NOT NULL;

    -- Answered, it goes: however the name stops waiting (kept, refused or joined; in Discord, on
    -- the website or by a merge), one trigger covers every path. It deletes only the one row of
    -- the entry being written, in its own server, so it needs no rights beyond the writer's. A
    -- later migration that changes `status` with row-level security opened for memory_entities
    -- must open name_found too (CLAUDE.md: open every table a statement touches), or the rows of
    -- the names it answers stay.
    CREATE FUNCTION dmbot_name_found_answered() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        DELETE FROM name_found
            WHERE guild_id = NEW.guild_id AND campaign_id = NEW.campaign_id
              AND entity_id = NEW.id;
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER memory_entities_name_found_answered
        AFTER UPDATE OF status ON memory_entities
        FOR EACH ROW WHEN (OLD.status = 'proposed' AND NEW.status <> 'proposed')
        EXECUTE FUNCTION dmbot_name_found_answered();
    """ + _isolate("name_found")

ISOLATED_TABLES: tuple[str, ...] = ("name_found",)
