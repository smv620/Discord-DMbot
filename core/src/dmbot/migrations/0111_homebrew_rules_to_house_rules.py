"""0111_homebrew_rules_to_house_rules

#1628.
"""

from __future__ import annotations

HOMEBREW_RULES_TO_HOUSE_RULES = """
    -- Rules are house rules, not homebrew (owner decision 2026-10-16, #1628; docs/PLAN.md,
    -- "Homebrew (part 1)"). Every homebrew entry of the kind 'rule' that is the DM's own
    -- and fits a house rule (one line of at most 500 characters, the name in front unless the
    -- text already starts with it; at most 200 house rules a campaign)
    -- becomes the campaign's next numbered house rule, and the entry goes. A rule from a shared
    -- book keeps its book confirmation, which a house rule cannot hold; one that does not fit
    -- and one past the 200 would lose their words: all stay as homebrew, retagged 'other'. A
    -- hidden rule moves too (owner, 2026-10-16, "Homebrew (part 2)": nothing is hidden from
    -- players any more). A linked house-rules file is not rewritten. Open suggestions
    -- of a 'rule' to a pack are dropped (a pack cannot hold one). The kind 'rule' leaves
    -- homebrew_entries' list.
    -- Row-level security (CLAUDE.md, migrations that change rows): migrations run with no server
    -- set, so each table read or written gets a transient policy that is dropped again below:
    -- homebrew_entries (read, delete, retag), house_rules (insert), campaigns (the counter) and
    -- pack_suggestions (delete).
    CREATE POLICY migrate_backfill ON homebrew_entries USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON house_rules USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON campaigns USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON pack_suggestions USING (true) WITH CHECK (true);

    CREATE TEMP TABLE moved_rules ON COMMIT DROP AS
        SELECT id, guild_id, campaign_id, created_by, created_at, updated_at, rule_text,
               row_number() OVER (PARTITION BY guild_id, campaign_id ORDER BY id) AS n
        FROM (
            SELECT id, guild_id, campaign_id, created_by, created_at, updated_at,
                   btrim(regexp_replace(
                       CASE WHEN lower(left(text, char_length(name))) = lower(name)
                            THEN text ELSE name || ': ' || text END,
                       '[[:space:]]+', ' ', 'g')) AS rule_text
            FROM homebrew_entries
            WHERE kind = 'rule' AND NOT from_book
        ) AS composed
        WHERE char_length(rule_text) <= 500;
    DELETE FROM moved_rules m
        WHERE m.n + (SELECT count(*) FROM house_rules h
                     WHERE h.guild_id = m.guild_id AND h.campaign_id = m.campaign_id) > 200;

    INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by, created_at,
                             updated_at)
        SELECT m.guild_id, m.campaign_id, c.house_rules_made + m.n, m.rule_text, m.created_by,
               m.created_at, m.updated_at
        FROM moved_rules m JOIN campaigns c ON c.guild_id = m.guild_id AND c.id = m.campaign_id;
    UPDATE campaigns c SET house_rules_made = c.house_rules_made + k.moved
        FROM (SELECT guild_id, campaign_id, count(*) AS moved FROM moved_rules
              GROUP BY guild_id, campaign_id) k
        WHERE c.guild_id = k.guild_id AND c.id = k.campaign_id;
    DELETE FROM homebrew_entries h USING moved_rules m WHERE h.id = m.id;
    UPDATE homebrew_entries SET kind = 'other' WHERE kind = 'rule';
    DELETE FROM pack_suggestions WHERE kind = 'rule' AND status = 'open';

    ALTER TABLE homebrew_entries DROP CONSTRAINT homebrew_entries_kind_check;
    ALTER TABLE homebrew_entries ADD CONSTRAINT homebrew_entries_kind_check
        CHECK (kind IN ('spell', 'monster', 'item', 'feat', 'condition', 'other'));

    DROP POLICY migrate_backfill ON homebrew_entries;
    DROP POLICY migrate_backfill ON house_rules;
    DROP POLICY migrate_backfill ON campaigns;
    DROP POLICY migrate_backfill ON pack_suggestions;
"""

SQL = HOMEBREW_RULES_TO_HOUSE_RULES
