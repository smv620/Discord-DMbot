"""0406_trait_offers

A lore upload's lineage or class for a character the campaign has already, waiting for the DM
(#3926).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Supervisor's ruling on #3926: a lore read's lineage or class for an existing, confirmed character
# is never written silently; it waits here as a question ("The lore says Torvin is a dwarf. Keep
# it?") until the DM answers in private. ✅ Keep it writes the rules link as the DM's own change
# (one Undo) and deletes the row; ❌ Not this sets `state = 'never'`, so the same trait is never
# asked about that character again. "Undo this upload" deletes the waiting rows it left
# (`notice_id`). `quote` is the reader's quote of the DM's own text, at most 240 characters, shown
# to the DM alone (as `memory_link_sources.quote`). Not carried in a backup (`campaigns/tables.py`):
# a question is made again by the next read. No row is changed.
SQL = r"""
    CREATE TABLE memory_trait_offers (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        entity_id      TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK (kind IN ('species', 'class')),
        name           TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
        name_key       TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 100),
        rules_source   TEXT NOT NULL DEFAULT '' CHECK (char_length(rules_source) <= 40),
        edition        TEXT CHECK (edition IN ('2014', '2024')),
        known          BOOLEAN NOT NULL DEFAULT FALSE,
        CHECK (known = (rules_source <> '')),
        quote          TEXT NOT NULL DEFAULT '' CHECK (char_length(quote) <= 240),
        upload         TEXT NOT NULL DEFAULT '' CHECK (char_length(upload) <= 200),
        notice_id      TEXT CHECK (notice_id ~ '^[0-9a-f]{32}$'),
        state          TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'never')),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_trait_offers_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, entity_id, kind, name_key),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_trait_offers_notice ON memory_trait_offers (guild_id, notice_id)
        WHERE notice_id IS NOT NULL;
    CREATE TRIGGER memory_trait_offers_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_trait_offers
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_trait_offers")

ISOLATED_TABLES: tuple[str, ...] = ("memory_trait_offers",)
