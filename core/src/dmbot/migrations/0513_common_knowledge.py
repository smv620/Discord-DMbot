"""0513_common_knowledge

Common knowledge (#4537, docs/design/party_knowledge.md §4): the "everyone" point of view, and its
rows for the names already stored from a World read.
"""

from __future__ import annotations

# - `memory_perspectives` gains a fourth campaign-wide kind, 'everyone' (no entry of its own, one
#   per campaign, like The truth and The party). A row it holds is known to every character,
#   player character or NPC, in the party or not, now or later (the gate's `reach`). Every campaign
#   that has The truth gets one now; a campaign made later gets one the first time a write needs it
#   (`beliefs.everyone_id`).
# - The move (§4, and open question 4 answered on #4537): only a name whose stored source is a
#   website upload read as World general knowledge (`memory_name_sources.upload_key` joined to
#   `lore_uploads.choice = 'world'`) can be told apart, so only those get a row for "everyone", on
#   the live entry the name became (a merged one followed to the entry it went into). Nothing else
#   is guessed: it stays with the characters who know it, and the DM can mark it "Everyone knows
#   this". Only adds rows; a second run adds nothing.
# Opens row-level security for itself (CLAUDE.md, #704) on every table it reads or writes. The count
# of rows added is raised as a notice, counts only.
SQL = r"""
    ALTER TABLE memory_perspectives DROP CONSTRAINT memory_perspectives_kind_check;
    ALTER TABLE memory_perspectives ADD CONSTRAINT memory_perspectives_kind_check
        CHECK (kind IN ('truth', 'party', 'pc', 'npc', 'everyone'));
    ALTER TABLE memory_perspectives DROP CONSTRAINT memory_perspectives_check;
    ALTER TABLE memory_perspectives ADD CONSTRAINT memory_perspectives_check
        CHECK ((kind IN ('truth', 'party', 'everyone') AND entity_id IS NULL)
            OR (kind IN ('pc', 'npc') AND entity_id IS NOT NULL));
    DROP INDEX memory_perspectives_one_each;
    CREATE UNIQUE INDEX memory_perspectives_one_each ON memory_perspectives
        (guild_id, campaign_id, kind) WHERE kind IN ('truth', 'party', 'everyone');

    CREATE POLICY migrate_backfill ON memory_perspectives FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_knowers FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_name_sources FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON lore_uploads FOR SELECT USING (true);

    DO $$
    DECLARE
        views BIGINT;
        rows_added BIGINT;
    BEGIN
        INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)
            SELECT t.guild_id, t.campaign_id,
                   md5(t.campaign_id || 'everyone' || clock_timestamp()::text),
                   'everyone', extract(epoch FROM now())::bigint
            FROM memory_perspectives t WHERE t.kind = 'truth'
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS views = ROW_COUNT;

        WITH RECURSIVE world AS (
            SELECT DISTINCT s.guild_id, s.campaign_id, s.entity_id AS id
            FROM memory_name_sources s
            JOIN lore_uploads u ON u.guild_id = s.guild_id AND u.campaign_id = s.campaign_id
                               AND u.id = s.upload_key
            WHERE u.choice = 'world'
        ), followed AS (
            SELECT w.guild_id, w.campaign_id, e.id, e.status, e.merged_into, 0 AS hops
            FROM world w JOIN memory_entities e
              ON e.guild_id = w.guild_id AND e.campaign_id = w.campaign_id AND e.id = w.id
            UNION ALL
            SELECT f.guild_id, f.campaign_id, e.id, e.status, e.merged_into, f.hops + 1
            FROM followed f JOIN memory_entities e
              ON e.guild_id = f.guild_id AND e.campaign_id = f.campaign_id
             AND e.id = f.merged_into
            WHERE f.status = 'merged' AND f.hops < 50
        )
        INSERT INTO memory_knowers (guild_id, campaign_id, id, perspective_id, about_entity_id,
                                    created_at, confidence)
            SELECT DISTINCT ON (f.guild_id, f.campaign_id, f.id)
                   f.guild_id, f.campaign_id,
                   md5(random()::text || clock_timestamp()::text || f.id),
                   v.id, f.id, extract(epoch FROM now())::bigint, 1.0
            FROM followed f
            JOIN memory_perspectives v ON v.guild_id = f.guild_id
                                      AND v.campaign_id = f.campaign_id AND v.kind = 'everyone'
            WHERE f.status NOT IN ('merged', 'rejected')
              AND NOT EXISTS (
                SELECT 1 FROM memory_knowers k
                WHERE k.guild_id = f.guild_id AND k.campaign_id = f.campaign_id
                  AND k.perspective_id = v.id AND k.about_entity_id = f.id)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS rows_added = ROW_COUNT;
        RAISE NOTICE 'common knowledge: % views made, % name rows added', views, rows_added;
    END $$;

    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_knowers;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_name_sources;
    DROP POLICY migrate_backfill ON lore_uploads;
    """
