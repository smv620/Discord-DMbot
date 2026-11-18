"""0536_forget_map_version

Account deletion moves the memory version of each campaign whose map facts it changed, and keeps a
fact another usable map still shows (#4711, follow-up to #4693, Supervisor's review item 3 and
his later ruling; #4696 is the same rule for a removed map).
"""

from __future__ import annotations

from importlib import import_module

# `dmbot_forget_maps()` (0534) deletes and changes `memory_map_facts` rows straight from SQL, so a
# page that caches what the campaign's notes say (the World notes page) kept showing the deleted
# reads until something else moved `campaigns.memory_version`, the number every store write moves
# (and announces on the `dmbot_memory` channel: flag 0, as a write to this table sends; flag 1 is
# "names changed", for the tables the name lookup reads, see `memory/notify.py`). This replaces the
# function once more so that it
# does the same, once for each campaign whose facts it touched, however many rows changed there.
# The set of campaigns is taken before the facts change (a kept row loses its map pointer, which is
# how it is found), as an array of "guild:campaign" texts, and the bump runs before the maps are
# deleted, because the policies below find the person's campaigns through their maps.
#
# A FACT ANOTHER MAP STILL SHOWS (#4696, the rule `MapStore._forget_said` and `PictureStore.delete`
# follow): before the three steps of 0534, a fact on a map the person uploaded that another usable
# map's area still shows (`map_features.fact_id`, not an area the DM turned down, and a map that is
# not itself among the person's) is kept and re-pointed to the OLDEST such map, whoever said it, its
# status and source unchanged; only a fact no other usable map shows goes by the three-case rule.
# Account deletion has no Undo, so the re-point is not logged.
# Policies for the re-point (function owner only, while it runs): other usable maps and every pin
# are readable, and `memory_map_facts`' read and update policies (0534) are replaced so a row may
# end up pointing at another usable map: Postgres checks the updated row against both.
#
# Two policies on `campaigns` for the function's owner only, open only while the function runs (its
# own `dmbot.owner_sync` value, as 0480 and 0534 do): the person's campaigns are read, and their
# version column is updated; nothing else in the row, nobody else's campaign. No table changes; the
# function keeps its owner, search path, REVOKE and web grant.
_first = import_module("dmbot.migrations.0534_forget_map_reads")
_GATE, _ME, _MY_MAPS, _log = _first._GATE, _first._ME, _first._MY_MAPS, _first._log
_MINE_CAMPAIGN = (
    "EXISTS (SELECT 1 FROM campaign_maps m WHERE m.guild_id = campaigns.guild_id"
    f" AND m.campaign_id = campaigns.id AND m.right_by = {_ME})"
)

_OTHER = (
    "map_id IN (SELECT m.id FROM campaign_maps m WHERE m.guild_id = memory_map_facts.guild_id"
    " AND m.campaign_id = memory_map_facts.campaign_id AND m.use_ok IS TRUE"
    f" AND m.right_by <> {_ME})"
)
_MINE = _first._MINE

SQL = (
    r"""
    DO $do$
    BEGIN
        DROP POLICY forget_map_reads_read ON memory_map_facts;
        DROP POLICY forget_map_reads_update ON memory_map_facts;
"""
    + f"""
        EXECUTE format('CREATE POLICY forget_map_reads_read ON memory_map_facts FOR SELECT TO %I
            USING ({_GATE} AND ({_MINE} OR map_id IS NULL OR {_OTHER}))', current_user);
        EXECUTE format('CREATE POLICY forget_map_reads_update ON memory_map_facts FOR UPDATE TO %I
            USING ({_GATE} AND {_MINE})
            WITH CHECK ({_GATE} AND (map_id IS NULL OR {_OTHER}))', current_user);
        EXECUTE format('CREATE POLICY forget_map_other_maps ON campaign_maps FOR SELECT TO %I
            USING ({_GATE} AND use_ok IS TRUE)', current_user);
        EXECUTE format('CREATE POLICY forget_map_features_read ON map_features FOR SELECT TO %I
            USING ({_GATE})', current_user);
        EXECUTE format('CREATE POLICY forget_map_version_read ON campaigns FOR SELECT TO %I
            USING ({_GATE} AND {_MINE_CAMPAIGN})', current_user);
        EXECUTE format('CREATE POLICY forget_map_version_update ON campaigns FOR UPDATE TO %I
            USING ({_GATE} AND {_MINE_CAMPAIGN}) WITH CHECK ({_GATE})', current_user);
"""
    + r"""
    END
    $do$;
    CREATE OR REPLACE FUNCTION dmbot_forget_maps() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        touched TEXT[];
        moved RECORD;
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_maps', true);
        -- the log rows first: some are found through the maps, which must still be there
        DELETE FROM memory_changes WHERE """
    + _log("'")
    + r""";
        -- the campaigns whose facts are about to change, found while the pointers are still set
        SELECT array_agg(DISTINCT f.guild_id::text || ':' || f.campaign_id) INTO touched
          FROM memory_map_facts f JOIN campaign_maps m
            ON m.guild_id = f.guild_id AND m.campaign_id = f.campaign_id AND f.map_id = m.id
         WHERE m.right_by = dmbot_current_user();
        -- a fact another usable map's area still shows is kept, on the oldest such map
        UPDATE memory_map_facts t SET map_id = o.map_id
          FROM (SELECT DISTINCT ON (f.fact_id) f.fact_id, f.guild_id, f.campaign_id,
                       m.id AS map_id
                  FROM map_features f JOIN campaign_maps m
                    ON m.guild_id = f.guild_id AND m.campaign_id = f.campaign_id
                   AND m.id = f.map_id
                 WHERE m.use_ok IS TRUE AND m.right_by <> dmbot_current_user()
                   AND f.fact_id IS NOT NULL AND f.status <> 'rejected'
                 ORDER BY f.fact_id, m.created_at, m.id) o
         WHERE t.guild_id = o.guild_id AND t.campaign_id = o.campaign_id AND t.id = o.fact_id
           AND t.map_id IN (SELECT id FROM campaign_maps m
                             WHERE m.guild_id = t.guild_id AND m.campaign_id = t.campaign_id
                               AND m.right_by = dmbot_current_user());
        -- what DMbot read only from the person's maps goes with them (its pins' pointer follows
        -- by the foreign key); a read the text also stated becomes the text's; the DM's word
        -- (and the DM's no) stays
        DELETE FROM memory_map_facts f USING campaign_maps m
         WHERE """
    + f"{_MY_MAPS}"
    + r""" AND f.status = 'read' AND f.source = 'map' AND NOT f.also_text;
        UPDATE memory_map_facts f SET source = 'text', also_text = false, map_id = NULL
          FROM campaign_maps m
         WHERE """
    + f"{_MY_MAPS}"
    + r""" AND f.status = 'read' AND f.source = 'map' AND f.also_text;
        UPDATE memory_map_facts f SET map_id = NULL FROM campaign_maps m
         WHERE """
    + f"{_MY_MAPS}"
    + r""";
        -- each such campaign's version moves once, and readers are told, as a store write does
        FOR moved IN
            WITH bumped AS (
                UPDATE campaigns SET memory_version = memory_version + 1
                 WHERE (guild_id::text || ':' || id) = ANY (touched)
                RETURNING id, memory_version
            )
            SELECT id, memory_version FROM bumped
        LOOP
            PERFORM pg_notify('dmbot_memory', moved.id || ':' || moved.memory_version || ':0');
        END LOOP;
        DELETE FROM campaign_maps WHERE right_by = dmbot_current_user();
        UPDATE campaign_maps SET use_by = 0 WHERE use_by = dmbot_current_user();
        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_maps() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_maps() FROM PUBLIC;
"""
)
