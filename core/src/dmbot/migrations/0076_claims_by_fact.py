"""0076_claims_by_fact

The recall service finds a fact's claim (#1350).
"""

from __future__ import annotations

MEMORY_CLAIMS_BY_FACT = """
    -- The recall service (#1350) finds the claim a fact came from by the fact's id; without this
    -- it would read every confirmed claim of the campaign to find them. An index only: no row
    -- changes, so no row-level-security opening is needed.
    CREATE INDEX memory_claims_by_fact ON memory_claims (guild_id, campaign_id, fact_id)
        WHERE fact_id IS NOT NULL;
    """

SQL = MEMORY_CLAIMS_BY_FACT
