"""0629_forget_pc_icon_pictures

A person's account deletion drops the icons that use a picture they posted (#3623).
"""

from __future__ import annotations

# A character's icon may be a kept picture (0628). The pictures' own forget step is still owed
# (#3274), so a deleted person's pictures still exist; what must not remain is a character drawn
# with them. `dmbot_forget_pc_icon_pictures()`, called from `accounts.delete_person`, deletes the
# `pc_icons` rows whose picture was posted by the person, so those characters read as the default
# again. The policies are for the function's owner only and open only while it runs (its own
# `dmbot.owner_sync` value, as 0480 does); the read policy on `campaign_pictures` shows only the
# person's own pictures, and only for this.
_GATE = "dmbot_owner_sync() = ''forget_pc_icon_pictures''"

SQL = (
    r"""
    DO $do$
    BEGIN
"""
    + f"""
        EXECUTE format('CREATE POLICY forget_pc_icon_pictures_read ON campaign_pictures
            FOR SELECT TO %I
            USING ({_GATE} AND poster_id = dmbot_current_user())', current_user);
        EXECUTE format('CREATE POLICY forget_pc_icon_pictures_read ON pc_icons FOR SELECT TO %I
            USING ({_GATE} AND custom_ref IN (SELECT id FROM campaign_pictures
                                              WHERE poster_id = dmbot_current_user()))',
            current_user);
        EXECUTE format('CREATE POLICY forget_pc_icon_pictures_delete ON pc_icons FOR DELETE TO %I
            USING ({_GATE} AND custom_ref IN (SELECT id FROM campaign_pictures
                                              WHERE poster_id = dmbot_current_user()))',
            current_user);
"""
    + r"""
    END
    $do$;
    CREATE FUNCTION dmbot_forget_pc_icon_pictures() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_pc_icon_pictures', true);
        DELETE FROM pc_icons WHERE custom_ref IN
            (SELECT id FROM campaign_pictures WHERE poster_id = dmbot_current_user());
        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_pc_icon_pictures()'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_pc_icon_pictures() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_pc_icon_pictures", "dmbot_forget_pc_icon_pictures()"),
)
