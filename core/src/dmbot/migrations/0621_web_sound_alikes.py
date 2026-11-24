"""0621_web_sound_alikes

The website's name card says "Sounds like X" as Discord's does (#5215).
"""

from __future__ import annotations

# `dmbot_web_sound_alikes(g, c, codes)` gives a DM of the campaign the confirmed names that share a
# sound code with a waiting name: the same rows `MemoryStore.sound_alikes` gives the bot (a
# confirmed entry's spellings that are not secret and not turned down, with the entry's own name),
# so the website works out "Sounds like X" with the card's own pure code (`memory/card_match.py`'s
# `card_sound_keys` and `near_match_in`) and never stores a match: a match stored goes stale when
# the known name is renamed, made secret or merged. Read-only, for the campaign's DMs only (NULL for
# anyone else), at most 40 codes and 500 rows, ids, spellings and names only.
SQL = r"""
    CREATE FUNCTION dmbot_web_sound_alikes(g BIGINT, c TEXT, codes TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF codes IS NULL OR cardinality(codes) = 0 OR cardinality(codes) > 40 THEN
            RETURN '[]'::jsonb;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object('id', r.entity_id, 'text', r.text, 'name', r.name)
                             ORDER BY r.entity_id, r.id)
            FROM (
                SELECT a.id, a.entity_id, a.text, e.name
                FROM memory_aliases a
                JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c AND a.status = 'confirmed'
                  AND NOT a.secret AND a.sound_codes && codes AND e.status = 'confirmed'
                ORDER BY a.entity_id, a.id
                LIMIT 500) r), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sound_alikes(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_sound_alikes(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_sound_alikes", "dmbot_web_sound_alikes(BIGINT, TEXT, TEXT[])"),
)
