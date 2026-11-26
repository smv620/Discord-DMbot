"""0623_web_look_alikes

The website can answer "Names that look alike" (#5259): its read door.
"""

from __future__ import annotations

# `dmbot_web_look_alike_names(g, c)` gives a DM of the campaign (NULL for anyone else) what
# `MemoryStore.look_alikes` reads before it groups: the campaign's confirmed names that can be
# compared (not a person at the table, not an unnamed one: #2790), with their kind (type and role),
# their spelling and how often they were heard, and the pairs the DM said are "Not the same". The
# website works the groups out with the bot's own pure finder (`memory/lookalikes.py`), so the two
# places list the same groups and the Questions count agrees with the list. It is for the DM alone:
# a name with a secret spelling is among them, as in Discord's look-alikes, which only a DM opens.
# Read-only; ids, names, kinds and counts only.
SQL = r"""
    CREATE FUNCTION dmbot_web_look_alike_names(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'name', e.name, 'type', e.type, 'role', e.role,
                           'heard', (SELECT count(*) FROM memory_mentions m
                                     WHERE m.guild_id = e.guild_id AND m.campaign_id = e.campaign_id
                                       AND m.entity_id = e.id))
                       ORDER BY e.id)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.status = 'confirmed'
                  AND e.type <> 'person' AND NOT e.unnamed), '[]'::jsonb),
            'apart', COALESCE((
                SELECT jsonb_agg(jsonb_build_array(n.a_id, n.b_id) ORDER BY n.a_id, n.b_id)
                FROM name_not_same n
                WHERE n.guild_id = g AND n.campaign_id = c), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_look_alike_names(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_look_alike_names(BIGINT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_look_alike_names", "dmbot_web_look_alike_names(BIGINT, TEXT)"),
)
