"""0027_handover_expiry."""

from __future__ import annotations

HANDOVER_EXPIRY = """
    -- An offer whose 7 days are up is announced once (#690): the owner is told, and the
    -- buttons come off the private message to the person offered (message_id). Offers
    -- already closed, or already past their 7 days, count as told, so nobody gets an old
    -- notice. end_told_at is set just before the owner is told, and cleared again if
    -- telling them failed for a moment (the next sweep tries again).
    ALTER TABLE campaign_handover_offers
        ADD COLUMN message_id  BIGINT,
        ADD COLUMN end_told_at BIGINT;
    -- Migrations run with no server set, so row-level security hides every row: open
    -- the table to this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaign_handover_offers
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaign_handover_offers SET end_told_at = COALESCE(decided_at, created_at)
        WHERE status <> 'open'
           OR created_at + 604800 <= extract(epoch FROM now())::BIGINT;
    DROP POLICY migrate_backfill ON campaign_handover_offers;
    -- The sweep's "ended, nobody told yet" (stays nearly empty).
    CREATE INDEX campaign_handover_offers_untold
        ON campaign_handover_offers (guild_id)
        WHERE status = 'expired' AND end_told_at IS NULL;
    """

SQL = HANDOVER_EXPIRY
