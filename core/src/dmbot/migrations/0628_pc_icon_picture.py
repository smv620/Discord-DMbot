"""0628_pc_icon_picture

A player character's icon can be a picture the campaign keeps (#3623, "Set a picture").
"""

from __future__ import annotations

# `pc_icons.custom_ref` was declared 32 hex characters (0367), which holds neither a kept picture's
# id (a number) nor its code, and no row has ever held `custom` (nothing could set it), so the
# column becomes the picture's id with a foreign key. A kept picture is only ever marked deleted,
# never removed, so the read door checks it is still there and still pickable, every time: a picture
# deleted, or made the DM's alone (`dm_only`), reads as no icon (the default order), whatever the
# row says. Rows that changed would need row-level security opened first (CLAUDE.md); there are
# none, and a `DELETE` of any `custom` row is wrapped the same way to be sure.
#
# The icon's address is a path of the website's own (`/me/campaigns/<ref>/pc-icons/<id>/picture`,
# which answers with the short redirect to the bucket), never the picture's public code, so a
# picture that stops being pickable simply stops resolving and its code is never in the graph.
#
# Who may pick (decided in SQL, from the signed-in user): the campaign's DM any pickable picture;
# a player, only a picture they posted themselves, for their own character. Pickable: this
# campaign's, kept (not deleted) and not `dm_only` (a picture from a DM's file is the DM's
# material, and an icon is drawn for everyone who sees the character).
#
# The three doors that already exist are changed by reading the live text back and replacing exact
# anchors, each found the stated number of times (as 0615 does for the queue door).
_READ_SWAPS = (
    # the icon a `custom` choice draws, only while its picture is still pickable
    (
        "                    WHEN r.choice = 'dndbeyond' THEN\n"
        "                         CASE WHEN r.portrait IS NOT NULL THEN\n"
        "                              jsonb_build_object('kind', 'dndbeyond', 'url', "
        "r.portrait) END\n",
        "                    WHEN r.choice = 'dndbeyond' THEN\n"
        "                         CASE WHEN r.portrait IS NOT NULL THEN\n"
        "                              jsonb_build_object('kind', 'dndbeyond', 'url', "
        "r.portrait) END\n"
        "                    WHEN r.choice = 'custom' AND r.picture_ok THEN\n"
        "                         jsonb_build_object('kind', 'picture',\n"
        "                              'url', '/me/campaigns/' || g || '.' || c || '/pc-icons/'\n"
        "                                     || r.id || '/picture')\n",
        1,
    ),
    # who is told which pictures exist: whether the caller has any picture to pick
    (
        "                                                 'dndbeyond', r.portrait IS NOT NULL))\n",
        "                                                 'dndbeyond', r.portrait IS NOT NULL,\n"
        "                                                 'pictures', r.pickable))\n",
        1,
    ),
    # a picture choice whose picture is gone or no longer pickable reads as no choice at all: the
    # default order (the player's avatar, else the D&D Beyond portrait)
    (
        "                    WHEN r.choice IS NULL AND r.shown IS NOT NULL THEN\n",
        "                    WHEN (r.choice IS NULL\n"
        "                          OR (r.choice = 'custom' AND NOT r.picture_ok))\n"
        "                         AND r.shown IS NOT NULL THEN\n",
        1,
    ),
    (
        "                SELECT e.id, i.choice, x.avatar_url, x.portrait,\n",
        "                SELECT e.id, i.choice, x.avatar_url, x.portrait,\n"
        "                       (pp.id IS NOT NULL) AS picture_ok, pk.any_pick AS pickable,\n",
        1,
    ),
    (
        "                     AND i.entity_id = e.id\n",
        "                     AND i.entity_id = e.id\n"
        "                LEFT JOIN campaign_pictures pp ON pp.id = i.custom_ref\n"
        "                     AND pp.guild_id = e.guild_id AND pp.campaign_id = e.campaign_id\n"
        "                     AND pp.deleted_at IS NULL AND NOT pp.dm_only\n"
        "                CROSS JOIN LATERAL (\n"
        "                    SELECT EXISTS (SELECT 1 FROM campaign_pictures q\n"
        "                        WHERE q.guild_id = e.guild_id AND q.campaign_id = e.campaign_id\n"
        "                          AND q.deleted_at IS NULL AND NOT q.dm_only\n"
        "                          AND (is_dm OR q.poster_id = dmbot_current_user())) AS any_pick\n"
        "                ) pk\n",
        1,
    ),
)

# The set door also says which picture a `custom` choice was, so the page's one Undo can write it
# back (through the new door below).
_SET_SWAPS = (
    (
        "        has_portrait BOOLEAN;\n    BEGIN\n",
        "        has_portrait BOOLEAN;\n        before_pic BIGINT;\n    BEGIN\n",
        1,
    ),
    (
        "        SELECT choice INTO before FROM pc_icons\n",
        "        SELECT choice, custom_ref INTO before, before_pic FROM pc_icons\n",
        1,
    ),
    (
        "        RETURN jsonb_build_object('status', 'ok', "
        "'previous', COALESCE(before, 'default'),\n"
        "                                  'now', pick);\n",
        "        RETURN jsonb_build_object('status', 'ok', "
        "'previous', COALESCE(before, 'default'),\n"
        "                                  'previousPicture', before_pic, 'now', pick);\n",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


SQL = (
    r"""
    -- No row holds `custom` today; the policy wrapper is only so the statement could not silently
    -- match nothing if one did.
    CREATE POLICY migrate_backfill ON pc_icons USING (true) WITH CHECK (true);
    DELETE FROM pc_icons WHERE choice = 'custom';
    DROP POLICY migrate_backfill ON pc_icons;
    ALTER TABLE pc_icons DROP CONSTRAINT pc_icons_custom_ref_check;
    ALTER TABLE pc_icons ALTER COLUMN custom_ref TYPE BIGINT USING NULL;
    ALTER TABLE pc_icons ADD CONSTRAINT pc_icons_custom_ref_fkey
        FOREIGN KEY (custom_ref) REFERENCES campaign_pictures (id) ON DELETE CASCADE;

    -- Choose a kept picture as the icon. The caller is the campaign's DM or the character's own
    -- player; the picture is one of this campaign's, kept, and not the DM's alone; a player may
    -- use only a picture they posted. Answers like the set door, with what it replaced.
    CREATE FUNCTION dmbot_web_pc_icon_picture(g BIGINT, c TEXT, ent TEXT, picture BIGINT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        who BIGINT := dmbot_current_user();
        played BIGINT;
        poster BIGINT;
        is_dm BOOLEAN;
        before TEXT;
        before_pic BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT e.played_by INTO played FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ent AND e.type = 'character'
              AND e.role = 'player_character' AND e.status = 'confirmed'
              AND e.merged_into IS NULL;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who);
        IF NOT (is_dm OR (played IS NOT NULL AND played = who)) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        SELECT p.poster_id INTO poster FROM campaign_pictures p
            WHERE p.id = picture AND p.guild_id = g AND p.campaign_id = c
              AND p.deleted_at IS NULL AND NOT p.dm_only;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'unavailable');
        END IF;
        IF NOT is_dm AND poster IS DISTINCT FROM who THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        SELECT choice, custom_ref INTO before, before_pic FROM pc_icons
            WHERE guild_id = g AND campaign_id = c AND entity_id = ent FOR UPDATE;
        INSERT INTO pc_icons (guild_id, campaign_id, entity_id, choice, custom_ref, set_by,
                              updated_at)
            VALUES (g, c, ent, 'custom', picture, who, now_s)
            ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET
                choice = 'custom', custom_ref = EXCLUDED.custom_ref, set_by = EXCLUDED.set_by,
                updated_at = EXCLUDED.updated_at;
        RETURN jsonb_build_object('status', 'ok', 'previous', COALESCE(before, 'default'),
                                  'previousPicture', before_pic, 'now', 'custom');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pc_icon_picture(BIGINT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pc_icon_picture(BIGINT, TEXT, TEXT, BIGINT) FROM PUBLIC;

    -- The file behind a character's picture icon, for the website's own redirect: the keys of the
    -- picture (preview first) while it is still pickable. For any member of the campaign's server
    -- (as the icon read is), NULL for anyone else or when there is none.
    CREATE FUNCTION dmbot_web_pc_icon_file(g BIGINT, c TEXT, ent TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN (
            SELECT jsonb_build_object('key', p.object_key, 'previewKey', p.preview_key)
            FROM pc_icons i
            JOIN campaign_pictures p ON p.id = i.custom_ref AND p.guild_id = i.guild_id
                 AND p.campaign_id = i.campaign_id AND p.deleted_at IS NULL AND NOT p.dm_only
            JOIN memory_entities e ON e.guild_id = i.guild_id AND e.campaign_id = i.campaign_id
                 AND e.id = i.entity_id AND e.type = 'character' AND e.role = 'player_character'
                 AND e.status = 'confirmed' AND e.merged_into IS NULL
            WHERE i.guild_id = g AND i.campaign_id = c AND i.entity_id = ent
              AND i.choice = 'custom');
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pc_icon_file(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pc_icon_file(BIGINT, TEXT, TEXT) FROM PUBLIC;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_pc_icons';"""
    + _steps(_READ_SWAPS)
    + r"""
        EXECUTE def;
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_pc_icon_set';"""
    + _steps(_SET_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_pc_icon_picture", "dmbot_web_pc_icon_picture(BIGINT, TEXT, TEXT, BIGINT)"),
    ("dmbot_web_pc_icon_file", "dmbot_web_pc_icon_file(BIGINT, TEXT, TEXT)"),
)
