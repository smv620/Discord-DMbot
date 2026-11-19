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
# The backfill marks what the change log can trace: a row of ONE player character's point of view,
# inserted in the same change that made a hidden connection (secret or not yet said at the table) or
# a secret other name, which is how a backstory's keep writes them. A 👥 Some know of one character
# on a kept connection looks the same and is marked too: that only keeps it from being copied (the
# careful side). Rows it cannot trace stay NULL. Migrations run unscoped and these tables force
# row-level security, so every table the statement reads is opened for it (CLAUDE.md, #704, #781).
SQL = r"""
    ALTER TABLE memory_knowers ADD COLUMN backstory_of TEXT
        CHECK (backstory_of ~ '^[0-9a-f]{32}$');
    ALTER TABLE memory_knowers ADD CONSTRAINT memory_knowers_backstory_of_fkey
        FOREIGN KEY (guild_id, campaign_id, backstory_of)
        REFERENCES memory_entities (guild_id, campaign_id, id)
        ON DELETE SET NULL (backstory_of) ON UPDATE CASCADE;

    CREATE POLICY migrate_backfill ON memory_knowers FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_perspectives FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);

    WITH written AS (
        SELECT ck.guild_id, ck.campaign_id, ck.batch, ck.row_id AS knower_id,
               ck.after->>'perspective_id' AS view_id,
               ck.after->>'relation_id' AS relation_id, ck.after->>'alias_id' AS alias_id
        FROM memory_changes ck
        WHERE ck.table_name = 'memory_knowers' AND ck.op = 'insert'
          AND (ck.after->>'relation_id' IS NOT NULL OR ck.after->>'alias_id' IS NOT NULL)
    ), born_hidden AS (
        SELECT w.* FROM written w
        JOIN memory_perspectives p ON p.guild_id = w.guild_id AND p.campaign_id = w.campaign_id
                                  AND p.id = w.view_id AND p.kind = 'pc'
        WHERE EXISTS (
            SELECT 1 FROM memory_changes cs
            WHERE cs.guild_id = w.guild_id AND cs.campaign_id = w.campaign_id
              AND cs.batch = w.batch AND cs.op = 'insert'
              AND ((cs.table_name = 'memory_relations' AND cs.row_id = w.relation_id
                    AND (COALESCE((cs.after->>'secret')::boolean, false)
                         OR COALESCE((cs.after->>'unrevealed')::boolean, false)))
                OR (cs.table_name = 'memory_aliases' AND cs.row_id = w.alias_id
                    AND COALESCE((cs.after->>'secret')::boolean, false))))
    ), lone AS (
        SELECT b.* FROM born_hidden b
        WHERE (SELECT count(*) FROM born_hidden o
               WHERE o.guild_id = b.guild_id AND o.campaign_id = b.campaign_id
                 AND o.batch = b.batch
                 AND o.relation_id IS NOT DISTINCT FROM b.relation_id
                 AND o.alias_id IS NOT DISTINCT FROM b.alias_id) = 1
    )
    UPDATE memory_knowers k SET backstory_of = p.entity_id
        FROM lone l
        JOIN memory_perspectives p ON p.guild_id = l.guild_id AND p.campaign_id = l.campaign_id
                                  AND p.id = l.view_id
        JOIN memory_entities e ON e.guild_id = p.guild_id AND e.campaign_id = p.campaign_id
                              AND e.id = p.entity_id
        WHERE k.guild_id = l.guild_id AND k.campaign_id = l.campaign_id AND k.id = l.knower_id;

    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_knowers;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
