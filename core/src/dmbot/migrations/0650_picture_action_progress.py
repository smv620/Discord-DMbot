"""0650_picture_action_progress

A picture action's progress for the Pictures page: when it last moved (#3355, route 6).
"""

from __future__ import annotations

# The page waits a few seconds for the bot's answer to a picture's Make private, Permanent or
# Delete, and when the bot has not answered it follows the action (CLAUDE.md, "Every wait shows
# that DMbot is still working"). So the row says when it last changed state: `updated_at`, set when
# the bot claims it and when it ends it (no heartbeat). A row from before it has 0, and the door
# reads that as "when it was asked". The poll door is replaced whole, with its search path set
# again, and also gives when the choice was asked, the server's own clock (so the page never counts
# from its own), and the action.
SQL = r"""
    ALTER TABLE picture_actions ADD COLUMN updated_at BIGINT NOT NULL DEFAULT 0
        CHECK (updated_at >= 0);

    CREATE OR REPLACE FUNCTION dmbot_web_picture_action(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r picture_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM picture_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF r.id IS NULL THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'note', r.note, 'action', r.action,
            'startedAt', r.created_at, 'updatedAt', GREATEST(r.updated_at, r.created_at),
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_picture_action(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""

ISOLATED_TABLES: tuple[str, ...] = ()
