"""0337_recap_request_progress

The recap page's notes and spoken notes say how long they have waited (#3355).
"""

from __future__ import annotations

# Every wait shows its stage, when it began and when it last moved (CLAUDE.md, "Every wait shows
# that DMbot is still working"). A note typed on the recap page and a clip spoken on it were the
# two waits that sent only their state. The rows already hold the times: `created_at` is when the
# page queued the request and `claimed_at` is when the bot took it (0 until then). The two read
# doors now send both, so the website can say how long a request has waited and whether the bot
# has taken it. Each is the original door with that one line widened and nothing else changed, and
# the door is still the caller's own request, in a server on their session's list (the voice
# one also the campaign's DMs'). No row is written; PUBLIC stays revoked and the search path is
# pinned again, because replacing a function resets it.
SQL = r"""
    CREATE OR REPLACE FUNCTION dmbot_recap_request_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer,
                                  'created_at', r.created_at, 'claimed_at', r.claimed_at);
    END
    $fn$;
    CREATE OR REPLACE FUNCTION dmbot_recap_voice_read(g BIGINT, c TEXT, at_ BIGINT, rid TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r recap_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM recap_requests x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.session_at = at_ AND x.id = rid
              AND x.kind = 'voice' AND x.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'answer', r.answer,
                                  'created_at', r.created_at, 'claimed_at', r.claimed_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_request_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_voice_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()
