"""0496_named_cultures

The campaign's named cultures (#4257, MapBot layers slice 3b; #4229's catalogue; Supervisor's
ruling on #4257, 2026-11-14).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# A culture the story names that isn't a lineage: "Illuskan", "Reghed", "Netherese". Kept beside the
# story graph, never as an entry (the `people` kind stays for a people that acts in the story,
# #4229). `name` is as the lore or the DM wrote it; `name_key` its plain lower-case form, one per
# campaign whatever its status, so a name the DM turned down stays turned down and is never read
# again. `lineage` optionally says whose it is ("Illuskan" is human), one of the lineage keys in
# `memory/place_peoples.py`, or ''. `status`: 'read' (DMbot found it in the lore; the DM keeps or
# turns it down in World notes) or 'confirmed' or 'rejected'; `source`: 'dm' or 'text'.
#
# The culture layer of `memory_map_facts` now holds exactly one of a lineage in `value` or a named
# culture in `culture_id` (then `value` is ''), as control holds a faction. Deleting a culture
# deletes its rows (design §5); the store logs that through `memory_changes` first, so it is one
# Undo. The open-row index takes `culture_id` in, so "Illuskan" and "Reghed" sit side by side.
#
# 3a's "Something else…" culture rows (`value = 'other'` with the DM's words) become named cultures
# here, as the DM's: the words are the name, the row points at it (Supervisor's ruling). Those are
# UPDATEs and INSERTs under forced row-level security, so the two tables are opened for this
# migration only (CLAUDE.md, #704). The population layer keeps `other` with words.
SQL = (
    r"""
    CREATE TABLE memory_cultures (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        name           TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        name_key       TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 60),
        lineage        TEXT NOT NULL DEFAULT '' CHECK (lineage ~ '^[a-z]{0,20}$'),
        status         TEXT NOT NULL CHECK (status IN ('read', 'confirmed', 'rejected')),
        source         TEXT NOT NULL CHECK (source IN ('dm', 'text')),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_cultures_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE UNIQUE INDEX memory_cultures_once
        ON memory_cultures (guild_id, campaign_id, name_key);
    CREATE TRIGGER memory_cultures_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_cultures
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """
    + _isolate("memory_cultures")
    + r"""
    ALTER TABLE memory_map_facts ADD COLUMN culture_id TEXT;
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_culture_fk
        FOREIGN KEY (guild_id, campaign_id, culture_id)
        REFERENCES memory_cultures (guild_id, campaign_id, id)
        ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE memory_map_facts DROP CONSTRAINT memory_map_facts_value_kind;
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_value_kind CHECK (
        (layer = 'control') = (faction_id IS NOT NULL)
        AND (layer = 'culture' OR culture_id IS NULL)
        AND (layer = 'control' OR culture_id IS NOT NULL) = (value = '')
    );
    DROP INDEX memory_map_facts_open;
    CREATE UNIQUE INDEX memory_map_facts_open
        ON memory_map_facts (guild_id, campaign_id, entity_id, layer, value,
                             COALESCE(faction_id, ''), COALESCE(culture_id, ''), lower(part))
        WHERE status <> 'rejected' AND to_session_at IS NULL;
    CREATE INDEX memory_map_facts_culture
        ON memory_map_facts (guild_id, campaign_id, culture_id) WHERE culture_id IS NOT NULL;

    CREATE POLICY migrate_backfill ON memory_map_facts FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_cultures FOR ALL USING (true) WITH CHECK (true);
    -- (one culture per campaign and name, its spelling from the first row that had it; kept when
    -- some open row has it, else turned down, as the DM had taken it away; the id is the name's
    -- own md5. Spaces are made single, as `place_peoples.culture_key` does.)
    INSERT INTO memory_cultures
        (guild_id, campaign_id, id, name, name_key, lineage, status, source, created_at)
    SELECT n.guild_id, n.campaign_id, md5(n.guild_id::text || '/' || n.campaign_id || '/' || n.key),
           (array_agg(n.name ORDER BY n.created_at, n.id))[1], n.key, '',
           CASE WHEN bool_or(n.open) THEN 'confirmed' ELSE 'rejected' END, 'dm', min(n.created_at)
      FROM (SELECT f.guild_id, f.campaign_id, f.id, f.created_at,
                   regexp_replace(btrim(f.words, E' \t\r\n'), '\s+', ' ', 'g') AS name,
                   lower(regexp_replace(btrim(f.words, E' \t\r\n'), '\s+', ' ', 'g')) AS key,
                   f.status <> 'rejected' AND f.to_session_at IS NULL AS open
              FROM memory_map_facts f
             WHERE f.layer = 'culture' AND f.value = 'other' AND btrim(f.words) <> '') n
     GROUP BY n.guild_id, n.campaign_id, n.key;
    UPDATE memory_map_facts f
       SET culture_id = c.id, value = '', words = ''
      FROM memory_cultures c
     WHERE f.layer = 'culture' AND f.value = 'other'
       AND c.guild_id = f.guild_id AND c.campaign_id = f.campaign_id
       AND c.name_key = lower(regexp_replace(btrim(f.words, E' \t\r\n'), '\s+', ' ', 'g'));
    DROP POLICY migrate_backfill ON memory_cultures;
    DROP POLICY migrate_backfill ON memory_map_facts;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ("memory_cultures",)
