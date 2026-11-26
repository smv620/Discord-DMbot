"""0566_web_pack_updates

The website's "House rule updates" for homebrew packs (#4841 part C, the packs slice; Supervisor's
ruling "sets first, packs second"): the newer versions of the packs a campaign follows, and the
updates DMbot has already taken, for the DM to see what changed.
"""

from __future__ import annotations

# Packs are followed like house-rules sets (`rule_set_follows`, kind 'homebrew', one row per pack,
# at most five a campaign). `dmbot_web_pack_updates(g, c)` gives each followed pack that still
# updates and has a newer version waiting (the copy the campaign has and the latest version, read
# exactly as the bot's `_latest_of` does: by the follow's share code through
# `dmbot_set_update_by_code`, else the pack's latest version in this server while it is shared),
# and each pack whose update DMbot already took and could still be undone (the copy before it,
# kept in the follow's own row, and the copy now). The difference is worked out in Python by
# `rules.pack_diff`, which Discord uses, so no diff rule is in SQL. A pack holds only the homebrew
# entries its publisher shared (no sourcebook text, by the pack rules) and this door returns
# nothing else of the other campaign: no story data, no share code. DM only (NULL for anyone
# else), for a campaign in a server on the session's list. The door changes no row.

SQL = r"""
    CREATE FUNCTION dmbot_web_pack_updates(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        f rule_set_follows%ROWTYPE;
        latest_version INTEGER;
        latest_content JSONB;
        found_set TEXT;
        waiting JSONB := '[]'::jsonb;
        taken JSONB := '[]'::jsonb;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        FOR f IN
            SELECT * FROM rule_set_follows r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.kind = 'homebrew'
            ORDER BY r.position, r.followed_at, r.set_id LIMIT 5
        LOOP
            IF f.previous_version IS NOT NULL AND f.previous_content IS NOT NULL THEN
                taken := taken || jsonb_build_array(jsonb_build_object(
                    'setId', f.set_id, 'name', f.set_name, 'publisher', f.publisher,
                    'from', f.previous_version, 'to', f.version,
                    'before', f.previous_content, 'after', f.content));
            END IF;
            IF NOT f.updating THEN
                CONTINUE;
            END IF;
            latest_version := NULL;
            IF f.code IS NOT NULL THEN
                SELECT u.set_id, u.version, u.content INTO found_set, latest_version, latest_content
                    FROM dmbot_set_update_by_code(f.code, f.followed_at) u;
                IF NOT FOUND OR found_set IS DISTINCT FROM f.set_id THEN
                    CONTINUE;
                END IF;
            ELSE
                SELECT v.version, v.content INTO latest_version, latest_content
                    FROM rule_sets s
                    JOIN rule_set_versions v ON v.guild_id = s.guild_id
                        AND v.campaign_id = s.campaign_id AND v.set_id = s.id
                    WHERE s.guild_id = g AND s.id = f.set_id AND s.status = 'shared'
                    ORDER BY v.version DESC LIMIT 1;
                IF NOT FOUND THEN
                    CONTINUE;
                END IF;
            END IF;
            IF latest_version > f.version THEN
                waiting := waiting || jsonb_build_array(jsonb_build_object(
                    'setId', f.set_id, 'name', f.set_name, 'publisher', f.publisher,
                    'from', f.version, 'to', latest_version,
                    'mine', f.content, 'latest', latest_content));
            END IF;
        END LOOP;
        RETURN jsonb_build_object('waiting', waiting, 'taken', taken);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pack_updates(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pack_updates(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_pack_updates", "dmbot_web_pack_updates(BIGINT, TEXT)"),
)
