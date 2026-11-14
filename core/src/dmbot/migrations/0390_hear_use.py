"""0390_hear_use

What 🔊 Hear it has spent in a month (#3818): a count of the voice company's credits per campaign.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Each press of Hear it that makes a new clip costs a few credits (one per character spoken), which
# the monthly count of narrated recaps (`recap_jobs`) cannot show: it counts recaps, not credits. So
# a campaign's month of Hear it is a count of its own, in credits and clips, kept per month
# (`month` is 202611 for November 2026, UTC) so the limit holds across restarts and copies of the
# bot. Numbers only: no words, no clip, no one's name. Rows go with their campaign and older months
# are cleared as a new one is counted. New table: no rows change, so no backfill policy.
SQL = r"""
    CREATE TABLE hear_use (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        month       INTEGER NOT NULL CHECK (month BETWEEN 200001 AND 999912),
        credits     INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
        clips       INTEGER NOT NULL DEFAULT 0 CHECK (clips >= 0),
        PRIMARY KEY (guild_id, campaign_id, month),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("hear_use")

ISOLATED_TABLES: tuple[str, ...] = ("hear_use",)
