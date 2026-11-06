"""0021_campaign_handover_offers."""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

HANDOVER_OFFERS = """
    -- Offers to hand a campaign over to another subscriber (#437 part 1b). Only the
    -- owner offers; ownership moves only when the person offered accepts, within 7 days
    -- (created_at + 7 days, checked on every read and accept), and only if they still
    -- have a free campaign slot then. One open offer per campaign. An open offer found
    -- past its 7 days is marked 'expired' when the next one is made. Not in backups:
    -- an offer is between two people, not part of the campaign's story.
    CREATE TABLE campaign_handover_offers (
        id           BIGSERIAL PRIMARY KEY,
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        from_user_id BIGINT NOT NULL,
        to_user_id   BIGINT NOT NULL CHECK (to_user_id <> from_user_id),
        created_at   BIGINT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'accepted', 'declined', 'withdrawn', 'expired')),
        decided_at   BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX campaign_handover_offers_one_open
        ON campaign_handover_offers (guild_id, campaign_id) WHERE status = 'open';
    -- Every offer of a campaign, for deleting it with the campaign (answered ones stay).
    CREATE INDEX campaign_handover_offers_by_campaign
        ON campaign_handover_offers (guild_id, campaign_id);
    """ + _isolate("campaign_handover_offers")

SQL = HANDOVER_OFFERS

ISOLATED_TABLES: tuple[str, ...] = ("campaign_handover_offers",)
