"""0345_rel_ties

The relationship reader's ties for a session, kept beside the old path for one comparison (#3244).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

REL_TIES = """
    -- What the relationship reader found in a session's script (#3242 stage 1, #3244 slice 6): one
    -- row per tie, with the two names, the type chosen, the reader's own type, the feeling word
    -- and the one sentence. DM-only working data and nothing else: no list, card, graph, recap,
    -- table answer or website door reads it; its only reader is dev1's comparison against the old
    -- reader (counts only). It can be made again, so it is never in a backup. Its sentences are
    -- people's words, so it goes with them: `user_ids` are everyone whose lines it was made from
    -- and "Delete my transcripts" removes every row that lists the person; `source_ids` are the
    -- transcript lines it came from (the lineage), and a deleted or changed line removes the rows
    -- (below), so no path can forget. A session's retention and the session itself remove them too.
    CREATE TABLE rel_ties (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        session_id TEXT NOT NULL,
        a          TEXT NOT NULL CHECK (a <> ''),
        b          TEXT NOT NULL CHECK (b <> ''),
        -- the type kept, the reader's own, and how the kept one was chosen ('embedding' or 'ai')
        kind       TEXT NOT NULL CHECK (kind <> ''),
        ai_kind    TEXT NOT NULL CHECK (ai_kind <> ''),
        chosen_by  TEXT NOT NULL CHECK (chosen_by IN ('embedding', 'ai')),
        word       TEXT,
        sentence   TEXT NOT NULL CHECK (sentence <> ''),
        user_ids   BIGINT[] NOT NULL CHECK (cardinality(user_ids) BETWEEN 1 AND 50),
        source_ids BIGINT[] NOT NULL CHECK (cardinality(source_ids) BETWEEN 1 AND 200),
        made_at    BIGINT NOT NULL CHECK (made_at > 0),
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX rel_ties_by_session ON rel_ties (guild_id, session_id);
    -- (forgetting a person, and a deleted line, find the rows by index)
    CREATE INDEX rel_ties_by_person ON rel_ties USING gin (user_ids);
    CREATE INDEX rel_ties_by_source ON rel_ties USING gin (source_ids);

    -- Like the script's own triggers (0317): erasing words is structural. A deleted line takes the
    -- ties that cite it; a change to what a line says, who said it or whose it is makes the whole
    -- session's ties out of date, so they go (they are made again on request).
    CREATE FUNCTION rel_ties_drop_for_deleted_line() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        DELETE FROM rel_ties
            WHERE guild_id = OLD.guild_id AND session_id = OLD.session_id
              AND source_ids @> ARRAY[OLD.id];
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER transcript_lines_drop_rel_ties
        AFTER DELETE ON transcript_lines
        FOR EACH ROW EXECUTE FUNCTION rel_ties_drop_for_deleted_line();
    CREATE FUNCTION rel_ties_drop_for_changed_line() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        DELETE FROM rel_ties
            WHERE guild_id = OLD.guild_id AND session_id = OLD.session_id;
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER transcript_lines_stale_rel_ties
        AFTER UPDATE OF heard, text, entity, user_id ON transcript_lines
        FOR EACH ROW
        WHEN (OLD.heard IS DISTINCT FROM NEW.heard OR OLD.text IS DISTINCT FROM NEW.text
              OR OLD.entity IS DISTINCT FROM NEW.entity OR OLD.user_id IS DISTINCT FROM NEW.user_id)
        EXECUTE FUNCTION rel_ties_drop_for_changed_line();
    """ + _isolate("rel_ties")

SQL = REL_TIES

ISOLATED_TABLES: tuple[str, ...] = ("rel_ties",)
