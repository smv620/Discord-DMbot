"""0402_name_sources

Which upload a name was read from (#3675 Part B), the name-level twin of `memory_link_sources`.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Supervisor's yes on #3675 (2026-11-06): a name read from lore or a backstory had `source =
# 'background'` and nothing saying which upload, so the Names page could not filter "found in this
# lore document". One row per (entry, upload), written when that upload's read made the entry; ids,
# the upload's label and a time only, never a word of the upload. The label is the DM's own name
# for a document and can name a private backstory ("Tavin's backstory"), so a door gives it to the
# campaign's DM alone, as 0352's does; no player door reads this table. Not carried in a backup
# (`campaigns/tables.py`): it is provenance, rebuilt by the next read, and reads as "Not known yet"
# after a restore. A merge leaves the rows on the merged-away entry: a reader follows
# `memory_entities.merged_into` (as `MemoryStore.name_sources` does), so an Undo of the merge needs
# nothing here. A door asking "the names this upload gave" must follow it forward the same way, to
# the live entry, or a merged name drops out of the filter. A list split into names copies its
# rows to each part (`_copy_name_sources`).
SQL = r"""
    CREATE TABLE memory_name_sources (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id      TEXT NOT NULL,
        -- the website's lore upload id when there is one, else the label itself: a re-read of the
        -- same upload is one row
        upload_key     TEXT NOT NULL CHECK (char_length(upload_key) BETWEEN 1 AND 120),
        label          TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_name_sources_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, entity_id, upload_key),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (the filter's own question: the names one upload gave)
    CREATE INDEX memory_name_sources_upload
        ON memory_name_sources (guild_id, campaign_id, upload_key);
    CREATE TRIGGER memory_name_sources_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_name_sources
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_name_sources")

ISOLATED_TABLES: tuple[str, ...] = ("memory_name_sources",)
