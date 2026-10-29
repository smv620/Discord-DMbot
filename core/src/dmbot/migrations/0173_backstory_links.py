"""0173_backstory_links

Connections a backstory found, waiting (#2380).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

BACKSTORY_LINKS = f"""
    -- The connections a backstory reading found between names (#2380; docs/PLAN.md, "Names at
    -- scale"), waiting for the DM to keep or refuse each. The same lifetime as the backstory's
    -- name suggestions: until the DM answers, the campaign is deleted, or an end is refused. Two
    -- entries, the relationship's key, a short quote of the DM's own text as evidence (never the
    -- text) and whether the text wrote it with a secret spelling. Deleted with either entry and
    -- with the campaign, left out of backups (a wait, not content), isolated per server.
    CREATE TABLE backstory_links (
        {_memory_scope()}
        subject_id TEXT NOT NULL,
        predicate  TEXT NOT NULL CHECK (char_length(predicate) BETWEEN 1 AND 80),
        object_id  TEXT NOT NULL,
        quote      TEXT NOT NULL CHECK (char_length(quote) <= 400),
        secret     BOOLEAN NOT NULL DEFAULT false,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, subject_id, predicate, object_id),
        CHECK (subject_id <> object_id),
        {_entity_link("subject_id")},
        {_entity_link("object_id")}
    );
    """ + _isolate("backstory_links")

SQL = BACKSTORY_LINKS

ISOLATED_TABLES: tuple[str, ...] = ("backstory_links",)
