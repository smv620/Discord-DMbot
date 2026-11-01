"""0025_handover_delivered."""

from __future__ import annotations

HANDOVER_DELIVERED = """
    -- When DMbot sent the private message about an offer (#690). An offer made on the
    -- website is saved with neither and announced on dmbot_handover_offers. The bot
    -- that serves the server claims it (claimed_at, for a few minutes: a send cut short
    -- is tried again once the claim lapses), sends the message, then sets delivered_at,
    -- so it's sent once. Every offer before this was made in Discord and sent there and
    -- then. A writer that leaves delivered_at empty gets its offer sent by the bot.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN delivered_at BIGINT,
        ADD COLUMN claimed_at   BIGINT;
    -- Migrations run with no server set, so row-level security hides every row: open
    -- the table to this one UPDATE (which also reads rows, hence FOR ALL), dropped again
    -- inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaign_handover_offers
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaign_handover_offers SET delivered_at = created_at;
    DROP POLICY migrate_backfill ON campaign_handover_offers;
    -- The bot's sweep when it starts listening: open offers not sent yet.
    CREATE INDEX campaign_handover_offers_undelivered
        ON campaign_handover_offers (guild_id)
        WHERE status = 'open' AND delivered_at IS NULL;
    """

SQL = HANDOVER_DELIVERED
