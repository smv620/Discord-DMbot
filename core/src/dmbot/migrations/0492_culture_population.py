"""0492_culture_population

A place's culture and who lives there (#4257, MapBot layers slice 3a; design
`docs/design/mapbot_layers.md` §6, Supervisor's ruling on #4257).
"""

from __future__ import annotations

# Two more layers on `memory_map_facts`, as 0457 left room for: 'culture' and 'population'. Both
# hold a lineage in `value` (a 2024 Player's Handbook species, lowercased, from the list in code,
# `memory/place_peoples.py`) or 'other' with the text's own words, never a number. A named culture
# from #4229's catalogue comes in slice 3b as a nullable `culture_id` beside `value`. `how` is the
# share: culture strong, some, traces or once; population all ("almost all"), most, many, some or
# few, or '' when none was said ("populated by halflings"). The layer check is read back from the
# table as it is and must be found exactly once, so a drift stops the migration. No row is changed.
SQL = r"""
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(c.oid) INTO STRICT d FROM pg_constraint c
            WHERE c.conrelid = 'memory_map_facts'::regclass AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) LIKE '%layer%'
              AND pg_get_constraintdef(c.oid) LIKE '%control%'
              AND pg_get_constraintdef(c.oid) NOT LIKE '%faction_id%';
        IF d IS NULL OR d NOT LIKE '%''control''::text]%' THEN
            RAISE EXCEPTION 'the layer list did not have the expected shape';
        END IF;
    END
    $do$;
    ALTER TABLE memory_map_facts DROP CONSTRAINT memory_map_facts_layer_check;
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_layer_check
        CHECK (layer IN ('terrain', 'climate', 'control', 'culture', 'population'));
    ALTER TABLE memory_map_facts ADD CONSTRAINT memory_map_facts_people_how CHECK (
        (layer <> 'culture' OR how IN ('strong', 'some', 'traces', 'once'))
        AND (layer <> 'population' OR how IN ('', 'all', 'most', 'many', 'some', 'few'))
    );
    """

ISOLATED_TABLES: tuple[str, ...] = ()
