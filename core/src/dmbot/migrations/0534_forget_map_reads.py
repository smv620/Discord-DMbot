"""0534_forget_map_reads

Account deletion applies the map deletion rule to the maps it takes (#4693; design
`docs/design/mapbot_layers.md` 7.5, as `MapStore._remove` keeps it: #4259 slice 8b).
"""

from __future__ import annotations

# `dmbot_forget_maps()` (0480) deletes the maps the departing person uploaded straight from SQL.
# The foreign keys on `memory_map_facts.map_id` and `map_features` only leave a floor behind
# (`ON DELETE SET NULL`), so DMbot's reads from a departed person's map would stay in the graph with
# no map behind them. Before the maps go, this replaces the function so that, for the facts that
# rest on those maps:
# - a `read` row that rests only on the map (`source = 'map'`, `also_text` false) is deleted (the
#   `map_features.fact_id` pointing at it goes to NULL by its own foreign key, SET NULL);
# - a `read` row the text also stated becomes the text's (`source = 'text'`, `also_text` false);
# - a `confirmed` or `rejected` row (the DM's own word, and the DM's no) keeps its value.
# Every row that is left loses its pointer (`map_id` NULL), which the foreign key would do anyway.
# Account deletion is not undoable, so none of this goes through the change log; the log rows that
# name the person's maps already go in 0480's function.
#
# The policies are for the function's owner only and open only while the function runs (its own
# `dmbot.owner_sync` value, as 0480 does). They read the person's maps through `campaign_maps`,
# which 0480's own policy opens for the same function. The read policy also shows a row the UPDATE
# leaves behind (`map_id` NULL), because Postgres checks the new row against the SELECT policies.
# No table changes; the function keeps its owner, its search path and its web grant.
_GATE = "dmbot_owner_sync() = ''forget_maps''"
_ME = "dmbot_current_user()"
_MINE = (
    "map_id IN (SELECT m.id FROM campaign_maps m WHERE m.guild_id = memory_map_facts.guild_id"
    f" AND m.campaign_id = memory_map_facts.campaign_id AND m.right_by = {_ME})"
)
# (the same test as 0480's, written for its own place: a doubled quote inside a policy's format
# string, a plain one inside the function)
_MY_MAPS = (
    "m.guild_id = f.guild_id AND m.campaign_id = f.campaign_id AND f.map_id = m.id"
    f" AND m.right_by = {_ME}"
)


def _log(quote: str) -> str:
    """0480's test for the person's map log rows, so the replaced function deletes the same ones.
    `quote` is how a quote mark is written where it is used."""
    q = quote
    named = " OR ".join(
        f"memory_changes.{side}->>{q}{col}{q} = {_ME}::text"
        for side in ("before", "after")
        for col in ("right_by", "use_by")
    )
    mine = (
        "m.guild_id = memory_changes.guild_id AND m.campaign_id = memory_changes.campaign_id"
        f" AND (m.right_by = {_ME} OR m.use_by = {_ME})"
    )
    return (
        f"table_name = {q}campaign_maps{q} AND (row_id IN"
        f" (SELECT m.id FROM campaign_maps m WHERE {mine}) OR {named})"
    )


SQL = (
    r"""
    DO $do$
    BEGIN
"""
    + f"""
        EXECUTE format('CREATE POLICY forget_map_reads_read ON memory_map_facts FOR SELECT TO %I
            USING ({_GATE} AND ({_MINE} OR map_id IS NULL))', current_user);
        EXECUTE format('CREATE POLICY forget_map_reads_delete ON memory_map_facts FOR DELETE TO %I
            USING ({_GATE} AND {_MINE} AND status = ''read'' AND source = ''map''
                   AND NOT also_text)', current_user);
        EXECUTE format('CREATE POLICY forget_map_reads_update ON memory_map_facts FOR UPDATE TO %I
            USING ({_GATE} AND {_MINE}) WITH CHECK ({_GATE})', current_user);
"""
    + r"""
    END
    $do$;
    CREATE OR REPLACE FUNCTION dmbot_forget_maps() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_maps', true);
        -- the log rows first: some are found through the maps, which must still be there
        DELETE FROM memory_changes WHERE """
    + _log("'")
    + r""";
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
