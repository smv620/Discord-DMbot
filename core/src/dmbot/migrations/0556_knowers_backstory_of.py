"""0556_knowers_backstory_of

Who-knows rows written for a backstory say whose backstory (#4795, sub-issue of #4537; Supervisor's
ruling on #4794, 2026-11-19; `docs/design/party_knowledge.md` §5: "another character's backstory"
is never copied).
"""

from __future__ import annotations

# `backstory_of`: the entry of the character whose backstory a who-knows row came from (the keep
# of a backstory's connection, a backstory's tie to an unnamed person, a backstory's secret name),
# NULL for every other row. The join copy ("What the party knows") and Catch up count a statement
# as known by the rest of the party only when one of them holds it unmarked, so in a party of two
# the other character's backstory is never copied. It names an entry of the same campaign; deleting
# that entry clears the mark (`ON DELETE SET NULL`), and a campaign moved to another server carries
# it (`ON UPDATE CASCADE`, #2550). Backups carry it (`memory/backup.py`).
#
# The backfill marks what the change log can trace, in two shapes (each a row of ONE player
# character's point of view; anything else stays NULL):
# 1. a row inserted in the same change that made a hidden connection (secret, or not yet said at the
#    table), as a backstory's keep writes it;
# 2. a row about an entry or a secret other name, in a change that wrote nothing but who-knows rows
#    for that one character, as a backstory section's names (`learn_statements`) and a backstory's
#    secret names (`set_alias_knowers`) write them.
# A 👥 Some know of one character, or the DM naming one character who learned one thing, looks the
# same and is marked too: that only keeps it from being copied (the careful side), never shows
# anything. One pass over the change log (a window count, no per-row subquery). Migrations run
# unscoped and these tables force row-level security, so every table the statements read is opened
# for them (CLAUDE.md, #704, #781). The index serves the key's own lookups (an entry deleted, a
# campaign moved).
SQL = r"""
    ALTER TABLE memory_knowers ADD COLUMN backstory_of TEXT
        CHECK (backstory_of ~ '^[0-9a-f]{32}$');
    ALTER TABLE memory_knowers ADD CONSTRAINT memory_knowers_backstory_of_fkey
        FOREIGN KEY (guild_id, campaign_id, backstory_of)
        REFERENCES memory_entities (guild_id, campaign_id, id)
        ON DELETE SET NULL (backstory_of) ON UPDATE CASCADE;

    CREATE INDEX memory_knowers_backstory_of ON memory_knowers (guild_id, campaign_id, backstory_of)
        WHERE backstory_of IS NOT NULL;

    CREATE POLICY migrate_backfill ON memory_knowers FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_perspectives FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);

    WITH written AS (
        SELECT ck.guild_id, ck.campaign_id, ck.batch, ck.row_id AS knower_id,
               ck.after->>'perspective_id' AS view_id, ck.after->>'relation_id' AS relation_id,
               ck.after->>'alias_id' AS alias_id, ck.after->>'about_entity_id' AS entry_id,
               (min(ck.after->>'perspective_id') OVER change
                = max(ck.after->>'perspective_id') OVER change) AS one_view
        FROM memory_changes ck
        WHERE ck.table_name = 'memory_knowers' AND ck.op = 'insert'
        WINDOW change AS (PARTITION BY ck.guild_id, ck.campaign_id, ck.batch)
    ), by_pc AS (
        SELECT w.*, p.entity_id,
               count(*) OVER (PARTITION BY w.guild_id, w.campaign_id, w.batch, w.relation_id,
                              w.alias_id, w.entry_id) AS on_statement
        FROM written w
        JOIN memory_perspectives p ON p.guild_id = w.guild_id AND p.campaign_id = w.campaign_id
                                  AND p.id = w.view_id AND p.kind = 'pc'
    ), traced AS (
        SELECT b.guild_id, b.campaign_id, b.knower_id, b.entity_id FROM by_pc b
        WHERE b.relation_id IS NOT NULL AND b.on_statement = 1
          AND EXISTS (
            SELECT 1 FROM memory_changes cs
            WHERE cs.guild_id = b.guild_id AND cs.campaign_id = b.campaign_id
              AND cs.batch = b.batch AND cs.op = 'insert'
              AND cs.table_name = 'memory_relations' AND cs.row_id = b.relation_id
              AND (COALESCE((cs.after->>'secret')::boolean, false)
                   OR COALESCE((cs.after->>'unrevealed')::boolean, false)))
        UNION ALL
        SELECT b.guild_id, b.campaign_id, b.knower_id, b.entity_id FROM by_pc b
        WHERE (b.alias_id IS NOT NULL OR b.entry_id IS NOT NULL) AND b.one_view
          AND NOT EXISTS (
            SELECT 1 FROM memory_changes cs
            WHERE cs.guild_id = b.guild_id AND cs.campaign_id = b.campaign_id
              AND cs.batch = b.batch
              AND (cs.op <> 'insert'
                   OR cs.table_name NOT IN ('memory_knowers', 'memory_alias_knowers')))
    )
    UPDATE memory_knowers k SET backstory_of = t.entity_id
        FROM traced t
        JOIN memory_entities e ON e.guild_id = t.guild_id AND e.campaign_id = t.campaign_id
                              AND e.id = t.entity_id
        WHERE k.guild_id = t.guild_id AND k.campaign_id = t.campaign_id AND k.id = t.knower_id;

    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_knowers;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
