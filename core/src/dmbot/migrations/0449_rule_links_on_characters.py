"""0449_rule_links_on_characters

Removes the links to the rules left on entries that are no longer characters (#4241).
"""

from __future__ import annotations

# Only a character is linked to the rules (its species, class, stat block…), and a backup holding a
# link on anything else was refused as damaged. A kind change and a merge into a non-character once
# left such links behind (`states.settle_kind` and `_move_rule_links` now clear them, and a restore
# leaves them out), so the links stored before this are removed here. An entry is a character when
# its kind is `character` or a campaign's own kind under it, however deep (`memory_types.parent`).
# Nothing else is touched. The rows go without a change-log entry, as any migration's do: an Undo of
# a kind change made before this brings the character back without its links, and an Undo of an
# older merge into a non-character that moved links is refused as "changed since". Both are
# rare, and the DM can add a link again. Migrations run unscoped and the three tables have forced
# row-level security, so each is opened for this statement and closed again (CLAUDE.md, #704).
SQL = r"""
    CREATE POLICY migrate_backfill ON memory_rule_links FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_types FOR ALL USING (true) WITH CHECK (true);

    WITH RECURSIVE character_kinds (guild_id, campaign_id, key) AS (
        SELECT DISTINCT guild_id, campaign_id, 'character'::text FROM memory_rule_links
        UNION
        SELECT t.guild_id, t.campaign_id, t.key
        FROM memory_types t
        JOIN character_kinds k
            ON t.guild_id = k.guild_id AND t.campaign_id = k.campaign_id AND t.parent = k.key
    )
    DELETE FROM memory_rule_links l
    USING memory_entities e
    WHERE e.guild_id = l.guild_id AND e.campaign_id = l.campaign_id AND e.id = l.entity_id
      AND NOT EXISTS (
          SELECT 1 FROM character_kinds k
          WHERE k.guild_id = e.guild_id AND k.campaign_id = e.campaign_id AND k.key = e.type
      );

    DROP POLICY migrate_backfill ON memory_rule_links;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_types;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
