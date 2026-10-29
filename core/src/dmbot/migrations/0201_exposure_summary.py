"""0201_exposure_summary

The summary applies exposures (#2410).
"""

from __future__ import annotations

EXPOSURE_SUMMARY = """
    -- The after-session summary applies what a session gave away (#2410, slice 2): an exposure
    -- becomes 'applied' (the secret was shown to the players, in the change-log batch kept here so
    -- Recant can undo exactly that) or 'recanted' (the DM took it back). New states and a column
    -- with no default needed: no row changes. 'applying' is a row claimed while its secret is being
    -- shown, so a Recant pressed meanwhile cannot slip between the claim and the showing.
    ALTER TABLE secret_exposures ADD COLUMN batch BIGINT;
    ALTER TABLE secret_exposures ADD COLUMN claimed_at BIGINT;
    ALTER TABLE secret_exposures DROP CONSTRAINT secret_exposures_status_check;
    ALTER TABLE secret_exposures ADD CONSTRAINT secret_exposures_status_check
        CHECK (status IN ('pending', 'settled', 'applying', 'applied', 'recanted'));
"""

SQL = EXPOSURE_SUMMARY
