"""0023_handover_offer_indexes."""

from __future__ import annotations

HANDOVER_OFFER_INDEXES = """
    -- The account page reads a person's open offers on every visit (#614): by who they
    -- were offered to, and by who offered them.
    CREATE INDEX campaign_handover_offers_open_to
        ON campaign_handover_offers (to_user_id) WHERE status = 'open';
    CREATE INDEX campaign_handover_offers_open_from
        ON campaign_handover_offers (from_user_id) WHERE status = 'open';
"""

SQL = HANDOVER_OFFER_INDEXES
