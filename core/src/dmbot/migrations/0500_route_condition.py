"""0500_route_condition

Whether a road, pass or bridge is open, hard going, closed or gone (#4258, MapBot layers slice 4a;
design `docs/design/mapbot_layers.md` §5 to §7, Supervisor's ruling on #4258, 2026-11-15).
"""

from __future__ import annotations

# A sixth layer on `memory_map_facts`, as 0457 left room for: 'condition'. Its value is one of
# open, hard ("hard going"), closed or gone, or 'other' with the text's own words; never a `how`.
#
# A condition is about a route, so its row names exactly one of (design §5):
# - `entity_id`: a route place ("the Eastway", "Icewind Pass": a place whose form is a route);
# - `relation_id` (new): a `route_to` connection whose `route_state` is '' (known): "the road
#   between Bryn Shander and Targos". Never a travelled or planned leg, which is a journey, not a
#   road (§6); a trigger refuses it, since a check can't look at another table. Only a condition
#   ever names a connection. `entity_id` becomes nullable for it, and "exactly one" is a check.
# The connection's foreign key goes with it (`ON DELETE CASCADE`; the store deletes them first,
# logged, and an Undo that would delete the road is refused while it has one, so the cascade never
# deletes a row unlogged) and follows a campaign moved to another server (#2550). The
# open-row index takes `relation_id` in, so the place's foreign key gets an index of its own.
# Condition is the first layer whose change in the story
# closes the old row as history (`to_session_at`) instead of rejecting it (Supervisor's point 2);
# that needs nothing new here. The layer check is read back from the table as it is and must be
# found exactly once, so a drift stops the migration. No row is changed.
SQL = r"""
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(c.oid) INTO STRICT d FROM pg_constraint c
            WHERE c.conrelid = 'memory_map_facts'::regclass
              AND c.conname = 'memory_map_facts_layer_check';
        IF d NOT LIKE '%''population''::text]%' OR d LIKE '%condition%' THEN
            RAISE EXCEPTION 'the layer list did not have the expected shape';
        END IF;
    END
    $do$;
    ALTER TABLE memory_map_facts DROP CONSTRAINT memory_map_facts_layer_check;
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_layer_check
        CHECK (layer IN ('terrain', 'climate', 'control', 'culture', 'population', 'condition'));
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_condition_value CHECK (
        layer <> 'condition'
        OR (value IN ('open', 'hard', 'closed', 'gone', 'other') AND how = '')
    );
    ALTER TABLE memory_map_facts ADD COLUMN relation_id TEXT;
    ALTER TABLE memory_map_facts ALTER COLUMN entity_id DROP NOT NULL;
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_route_fk
        FOREIGN KEY (guild_id, campaign_id, relation_id)
        REFERENCES memory_relations (guild_id, campaign_id, id)
        ON DELETE CASCADE ON UPDATE CASCADE;
    -- exactly one of a place or a connection, and a connection only for a condition
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_one_target CHECK (
        (entity_id IS NULL) <> (relation_id IS NULL)
        AND (relation_id IS NULL OR layer = 'condition')
    );
    DROP INDEX memory_map_facts_open;
    CREATE UNIQUE INDEX memory_map_facts_open
        ON memory_map_facts (guild_id, campaign_id, COALESCE(entity_id, ''),
                             COALESCE(relation_id, ''), layer, value, COALESCE(faction_id, ''),
                             COALESCE(culture_id, ''), lower(part))
        WHERE status <> 'rejected' AND to_session_at IS NULL;
    CREATE INDEX memory_map_facts_route
        ON memory_map_facts (guild_id, campaign_id, relation_id) WHERE relation_id IS NOT NULL;
    -- the place's foreign key had the open index for its checks; with COALESCE in it, it needs
    -- its own (deleting a campaign or a place checks every link; test_memory_store's Indexes)
    CREATE INDEX memory_map_facts_place ON memory_map_facts (guild_id, campaign_id, entity_id);

    CREATE FUNCTION dmbot_map_fact_known_route() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.relation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM memory_relations r
             WHERE r.guild_id = NEW.guild_id AND r.campaign_id = NEW.campaign_id
               AND r.id = NEW.relation_id AND r.predicate = 'route_to' AND r.route_state = ''
        ) THEN
            RAISE EXCEPTION 'a condition names only a known route'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_map_fact_known_route() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_map_fact_known_route() FROM PUBLIC;
    CREATE TRIGGER memory_map_facts_known_route
        BEFORE INSERT OR UPDATE OF relation_id ON memory_map_facts
        FOR EACH ROW EXECUTE FUNCTION dmbot_map_fact_known_route();
    """

ISOLATED_TABLES: tuple[str, ...] = ()
