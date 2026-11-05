"""0346_view_version_bumps

A change to who is the DM, or to how much of the DM screen players may see, moves the campaign's
memory version, so a page that asks "did the story change?" reads again (#3294).
"""

from __future__ import annotations

VIEW_VERSION_BUMPS = """
    -- `campaigns.memory_version` is the number the Story Graph page compares before it reads the
    -- whole graph again. Every memory write already moves it (`MemoryStore._write`). What a person
    -- SEES also depends on two settings that are not memory writes: who the DMs are, and whether
    -- players may peek behind the DM screen. They move the number here, in the database, so no
    -- code path (a command, the website, a handover, a restore) can forget. Nothing else in the
    -- row is touched, and a statement that already moved the version is left as it is.
    CREATE FUNCTION campaigns_bump_version_for_view() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.memory_version = OLD.memory_version THEN
            NEW.memory_version := OLD.memory_version + 1;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER campaigns_view_settings_version
        BEFORE UPDATE OF dm_screen_visibility, owner_user_id ON campaigns
        FOR EACH ROW
        WHEN (OLD.dm_screen_visibility IS DISTINCT FROM NEW.dm_screen_visibility
              OR OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id)
        EXECUTE FUNCTION campaigns_bump_version_for_view();

    CREATE FUNCTION campaign_dms_bump_version() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        UPDATE campaigns SET memory_version = memory_version + 1
            WHERE guild_id = COALESCE(NEW.guild_id, OLD.guild_id)
              AND id = COALESCE(NEW.campaign_id, OLD.campaign_id);
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER campaign_dms_version
        AFTER INSERT OR DELETE ON campaign_dms
        FOR EACH ROW EXECUTE FUNCTION campaign_dms_bump_version();
"""

SQL = VIEW_VERSION_BUMPS
