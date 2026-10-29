"""0045_campaign_guilds."""

from __future__ import annotations

CAMPAIGN_GUILDS = """
    -- Which servers have ever held a campaign (#989), so the daily retention job can reach
    -- the campaigns of servers the bot has since left: it can't ask Discord about those.
    -- Server IDs and nothing else, so no row-level security (a routing table; see the rules
    -- at the top of this file). A plain trigger fills it however a campaign is made or
    -- restored; the retention job removes a server once it has no campaigns left.
    CREATE TABLE campaign_guilds (
        guild_id BIGINT PRIMARY KEY
    );
    CREATE FUNCTION dmbot_note_campaign_guild() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        INSERT INTO campaign_guilds (guild_id) VALUES (NEW.guild_id) ON CONFLICT DO NOTHING;
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER campaign_guilds_note
        AFTER INSERT ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_campaign_guild();
    -- The servers that hold campaigns now. Migrations run with no server set, so row-level
    -- security hides every campaign: the backfill opens the one table it reads for itself
    -- (CLAUDE.md), dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    INSERT INTO campaign_guilds (guild_id) SELECT DISTINCT guild_id FROM campaigns;
    DROP POLICY migrate_backfill ON campaigns;
    """

SQL = CAMPAIGN_GUILDS

ROUTING_TABLES: tuple[str, ...] = ("campaign_guilds",)
