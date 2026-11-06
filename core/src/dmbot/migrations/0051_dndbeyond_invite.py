"""0051_dndbeyond_invite."""

from __future__ import annotations

DNDBEYOND_INVITE = """
    -- The DM's own D&D Beyond campaign invite link (#1119; docs/PLAN.md, "The D&D Beyond
    -- campaign invite"), passed on to players on a button. Only the one standard form is
    -- kept (dmbot.campaigns.invite checks it too). A nullable column: no row changes, so no
    -- backfill policy.
    ALTER TABLE campaigns ADD COLUMN dndbeyond_invite TEXT
        CHECK (dndbeyond_invite IS NULL OR dndbeyond_invite ~
            '^https://www[.]dndbeyond[.]com/campaigns/join/[A-Za-z0-9]{4,64}$');
"""

SQL = DNDBEYOND_INVITE
