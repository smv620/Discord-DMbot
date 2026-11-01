"""0232_link_which

"Which Mara?" waits for the DM's pick (#2650).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

BACKSTORY_WHICH = """
    -- A backstory connection whose one end fits two names equally ("Mara" when the campaign has
    -- Mara Ashgrove and Mara Voss) waits here for the DM to pick (#2650), instead of being left
    -- out. It holds the end that is a name (`other_id`, and which side of the connection it is),
    -- the end as written (`word`: the DM's own text, a name), the candidate entry ids and the
    -- same short quote and flags a waiting connection holds. Picking one moves it to
    -- `backstory_links` (ready as soon as both names are answered); "Neither" deletes it. Never
    -- the text itself. Same lifetime as `backstory_links`: deleted with the campaign or its name,
    -- left out of backups (a wait, not content). Its foreign keys follow a campaign that moves
    -- to another server (ON UPDATE CASCADE).
    CREATE TABLE backstory_which (
        guild_id         BIGINT NOT NULL,
        campaign_id      TEXT NOT NULL,
        id               TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$'),
        other_id         TEXT NOT NULL,
        other_is_subject BOOLEAN NOT NULL,
        predicate        TEXT NOT NULL CHECK (char_length(predicate) BETWEEN 1 AND 80),
        word             TEXT NOT NULL CHECK (char_length(word) BETWEEN 1 AND 80),
        candidates       TEXT[] NOT NULL CHECK (cardinality(candidates) BETWEEN 2 AND 10),
        quote            TEXT NOT NULL CHECK (char_length(quote) <= 400),
        secret           BOOLEAN NOT NULL DEFAULT false,
        detail           TEXT NOT NULL DEFAULT '',
        truth            BOOLEAN NOT NULL DEFAULT false,
        upload           TEXT NOT NULL DEFAULT '' CHECK (char_length(upload) <= 120),
        created_at       BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, other_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX backstory_which_once ON backstory_which
        (guild_id, campaign_id, other_id, other_is_subject, predicate, word);
    """ + _isolate("backstory_which")

SQL = BACKSTORY_WHICH

ISOLATED_TABLES: tuple[str, ...] = ("backstory_which",)
