"""0601_more_names

The names an imported session's name scan heard past its first 10, waiting for the DM behind
"More names" (#5055 B; Supervisor's ruling on GraphEngineer's design note).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- The name scan offers at most 10 new names from a session (`scan.MAX_SUGGESTIONS`). A live
    -- session keeps that short list; an imported one is read once, so what is past the 10 waits
    -- here instead of being lost, until the DM presses "📝 More names" (`memory/more_names.py`),
    -- which makes them names to check exactly as the scan does and deletes the rows. Not an entry:
    -- nothing that lists names reads this table. A row is a word heard at the table, as the scan
    -- read it from the lines of people recorded then, like the scan's own suggestions: it goes with
    -- its session (the key below) and with its transcript (`more_names.forget_session`), and is
    -- not carried in a backup (`campaigns/tables.py`: a request still waiting). `name_key` keeps
    -- one row a name in a session, so a scan run again changes nothing; `also` are the other ways
    -- it was said, grouped with it (`scan.group_alike`).
    CREATE TABLE import_more_names (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        session_id         TEXT NOT NULL,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        name               TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
        name_key           TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 200),
        times              INTEGER NOT NULL CHECK (times BETWEEN 1 AND 100000),
        also               TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(also) <= 10),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, session_id, name_key)
    );
    CREATE INDEX import_more_names_by_session ON import_more_names (guild_id, session_id);
    """ + _isolate("import_more_names")

ISOLATED_TABLES: tuple[str, ...] = ("import_more_names",)
