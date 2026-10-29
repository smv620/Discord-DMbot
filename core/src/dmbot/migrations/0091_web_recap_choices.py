"""0091_web_recap_choices

The DM's recap choices on the site (#1468).
"""

from __future__ import annotations

WEB_RECAP_CHOICES = """
    -- The DM's three recap choices on the website (#1468), the same as the DM screen's 📜 Recaps
    -- panel (#1421): recaps on or off, the narration and music, the email. The website's role has
    -- no right to write `campaigns`, so both the read and the write go through these doors. Each
    -- answers only a DM of the campaign (`campaign_dms` and `dmbot_current_user()`: never a server
    -- manager, as in the panel), in a server on the session's list, with that server set for the
    -- transaction. The choices can only turn things off in effect: the bot's own plan gate decides
    -- at the time of each recap (it fails closed), and the API refuses to turn the narration on for
    -- a plan that does not have it.
    CREATE FUNCTION dmbot_web_recap_choices(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        -- The owner decides the plan (the bot reads the owner's plan), so it comes back too: the
        -- API says whether the narration may be turned on only when the DM is that owner.
        RETURN jsonb_build_object(
            'text', camp.recaps_text, 'audio', camp.recaps_audio, 'email', camp.recaps_email,
            'owner', camp.owner_user_id::TEXT);
    END
    $fn$;

    -- Answers 'ok', 'not_allowed' (not this campaign's DM), 'not_found' or 'bad_choice'.
    CREATE FUNCTION dmbot_web_set_recap_choice(g BIGINT, c TEXT, which TEXT, turn_on BOOLEAN)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        IF which = 'text' THEN
            UPDATE campaigns SET recaps_text = turn_on WHERE guild_id = g AND id = c;
        ELSIF which = 'audio' THEN
            UPDATE campaigns SET recaps_audio = turn_on WHERE guild_id = g AND id = c;
        ELSIF which = 'email' THEN
            UPDATE campaigns SET recaps_email = turn_on WHERE guild_id = g AND id = c;
        ELSE
            RETURN 'bad_choice';
        END IF;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_choices(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_recap_choices(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SQL = WEB_RECAP_CHOICES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The DM's recap choices, read and written on the recap page (#1468): DM only.
    ("dmbot_web_recap_choices", "dmbot_web_recap_choices(BIGINT, TEXT)"),
    ("dmbot_web_set_recap_choice", "dmbot_web_set_recap_choice(BIGINT, TEXT, TEXT, BOOLEAN)"),
)
