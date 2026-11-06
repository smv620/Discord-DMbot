"""0022_handover_offer_names."""

from __future__ import annotations

HANDOVER_NAMES = """
    -- Both people's display names as they were when the offer was made (#437, decided
    -- 2026-10-08): the account page shows "Oskar wants to hand you…", and the website
    -- holds nobody's name but the signed-in person's. They go with the row (deleted
    -- with the campaign); nothing else stores them. Correction to 0021's note: an open
    -- offer past its 7 days is marked expired when the next one is made, and also when
    -- anyone accepts, declines or withdraws it.
    -- 100: campaigns.models.NAME_ON_OFFER_MAX.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN from_name TEXT NOT NULL DEFAULT '' CHECK (length(from_name) <= 100),
        ADD COLUMN to_name   TEXT NOT NULL DEFAULT '' CHECK (length(to_name) <= 100);
    ALTER TABLE campaign_handover_offers
        ALTER COLUMN from_name DROP DEFAULT,
        ALTER COLUMN to_name DROP DEFAULT;
    """

SQL = HANDOVER_NAMES
