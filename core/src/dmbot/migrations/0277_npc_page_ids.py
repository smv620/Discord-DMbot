"""0277_npc_page_ids

Which entries have an NPC page, so the story graph can link to it (#2887).
"""

from __future__ import annotations

# Which entries of a campaign have an NPC page (#2887). The story graph marks each name `page: true`
# when its page exists, so the website can link to it now that the Discord list is gone (#2874).
# The same source and the same checks as `dmbot_web_npc_page`: the entries in `npc_roster` (the
# players' view, written by the bot), for a campaign of a server the caller is in. Entry ids only:
# no names, no words, so nothing is revealed beyond "a page exists".
NPC_PAGE_IDS = r"""
    CREATE FUNCTION dmbot_web_npc_page_ids(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((SELECT jsonb_agg(r.entry_id ORDER BY r.entry_id) FROM npc_roster r
                         WHERE r.guild_id = g AND r.campaign_id = c), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_npc_page_ids(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_npc_page_ids(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = NPC_PAGE_IDS

# Which entries have such a page, so the story graph can link to it (#2887).
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_npc_page_ids", "dmbot_web_npc_page_ids(BIGINT, TEXT)"),
)
