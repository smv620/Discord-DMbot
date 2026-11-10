"""0410_trait_offers_read

The website lists the lineage and class questions waiting for the DM (#3946, the web half of #3926).
"""

from __future__ import annotations

# One read door, no new table. It gives the questions `TraitOfferStore.waiting` gives, for a DM of
# the campaign only (NULL for anyone else): the character, the trait, what DMbot has for them now,
# the upload's label and the reader's quote of the DM's own text (at most 240 characters). The quote
# and the label are the DM's own words and go to the DM alone, never to a player or a shared view.
# The same rows as the bot's own reader: a waiting question, of a confirmed character, whose answer
# is not already true. No row is changed; the answers are queued actions (a later migration).
SQL = r"""
    CREATE FUNCTION dmbot_web_trait_offers(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'entity', q.entity_id, 'who', q.who, 'kind', q.kind, 'name', q.name,
                'key', q.name_key, 'known', q.known, 'edition', q.edition,
                'current', q.current, 'classes', to_jsonb(q.classes),
                'upload', q.upload, 'quote', q.quote) ORDER BY q.created_at, q.entity_id,
                q.kind, q.name_key)
            FROM (
                SELECT o.entity_id, e.name AS who, o.kind, o.name, o.name_key, o.known, o.edition,
                       o.upload, o.quote, o.created_at,
                       (SELECT l.name FROM memory_rule_links l
                         WHERE l.guild_id = o.guild_id AND l.campaign_id = o.campaign_id
                           AND l.entity_id = o.entity_id AND l.kind = 'species' LIMIT 1) AS current,
                       ARRAY(SELECT l.name FROM memory_rule_links l
                              WHERE l.guild_id = o.guild_id AND l.campaign_id = o.campaign_id
                                AND l.entity_id = o.entity_id AND l.kind = 'class'
                              ORDER BY l.created_at, l.name) AS classes
                FROM memory_trait_offers o
                JOIN memory_entities e ON e.guild_id = o.guild_id
                    AND e.campaign_id = o.campaign_id AND e.id = o.entity_id
                WHERE o.guild_id = g AND o.campaign_id = c AND o.state = 'waiting'
                  AND e.status = 'confirmed'
                  AND NOT EXISTS (SELECT 1 FROM memory_rule_links l
                                   WHERE l.guild_id = o.guild_id AND l.campaign_id = o.campaign_id
                                     AND l.entity_id = o.entity_id AND l.kind = o.kind
                                     AND lower(l.name) = lower(o.name))
                ORDER BY o.created_at, o.entity_id, o.kind, o.name_key
                LIMIT 100) q), '[]'::JSONB);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_trait_offers(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_trait_offers(BIGINT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_trait_offers", "dmbot_web_trait_offers(BIGINT, TEXT)"),
)
