"""0398_channel_people

Who can see a campaign's DM Bot and transcript channels (#3891): the campaign's
`channels_visibility` ("people", the default, or "everyone"), and the list of members a DM added by
hand to the people who may see them.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# "people" is the default for new campaigns and, at the deploy, for existing ones: the owner's rule
# is that a campaign's channels are its people's unless the DM opens them (PLAN, "Who can see a
# campaign's channels"). A defaulted column changes no rows, so no row-level-security policy.
#
# The by-hand list holds Discord member ids only (no names, no text). It is not in a backup: a
# backup can be downloaded by anyone in the server, and these ids belong to this server's members.
SQL = r"""
    ALTER TABLE campaigns
        ADD COLUMN channels_visibility TEXT NOT NULL DEFAULT 'people'
        CHECK (channels_visibility IN ('people', 'everyone'));

    CREATE TABLE campaign_channel_people (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        added_by    BIGINT NOT NULL,
        added_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """ + _isolate("campaign_channel_people")

ISOLATED_TABLES: tuple[str, ...] = ("campaign_channel_people",)
