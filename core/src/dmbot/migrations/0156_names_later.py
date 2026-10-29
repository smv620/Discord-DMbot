"""0156_names_later

A "Later" that stops holding a recap (#2174).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

NAMES_LATER = f"""
    -- A suggested name the DM pressed "Later" on (#2174): the question stays in 📝 Check new names,
    -- but it no longer holds a recap back, so one name the DM cannot decide on does not keep
    -- every recap waiting. One row per name, deleted with the name and with the campaign, left
    -- out of backups (a wait, not content: a restored campaign's suggestions ask again), isolated
    -- per server. A name that is decided on loses its row (`Changes.update`). No speaker, no line,
    -- no words.
    CREATE TABLE name_later (
        {_memory_scope()}
        entity_id   TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        {_entity_link("entity_id")}
    );
    """ + _isolate("name_later")

SQL = NAMES_LATER

ISOLATED_TABLES: tuple[str, ...] = ("name_later",)
