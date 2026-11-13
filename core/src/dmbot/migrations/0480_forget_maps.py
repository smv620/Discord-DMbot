"""0480_forget_maps

Deleting an account removes the maps its person uploaded and clears the person from the maps they
answered (#3274, #4344: Supervisor's condition before MapBot slices 3b and 3c), in
`dmbot_forget_maps()`, called from `accounts.delete_person` after `dmbot_forget_person_ids()`.
"""

from __future__ import annotations

# `campaign_maps.right_by` is the person who said "this is a map I may use and keep", so the map is
# theirs and goes with them (the picture's own file is the pictures' forget step, still owed,
# #3274).
# `campaign_maps.use_by` is the DM who answered "use it?": the map stays, `use_ok` and `use_at` stay
# (DMbot's use of it is unchanged), and `use_by` goes to 0, "someone who has left", as 0432 does;
# its check of `> 0` is relaxed to `>= 0` from the table's own text.
#
# The change log (`memory_changes`) holds the title and both ids of every map write, so the log rows
# of every map the person made or answered go first: what stays of a map is its row, never a
# person's id in its history. (An Undo of such a map is then "too late", as for a deleted picture.)
#
# Every policy is for the function's owner only and open only while the function runs (its own
# `dmbot.owner_sync` value, as 0431 and 0455 do). The read policies also show the rows the UPDATE
# leaves behind (`use_by = 0`), because Postgres checks the new row against the SELECT policies.
_GATE = "dmbot_owner_sync() = ''forget_maps''"
_MINE_MAP = (
    "m.guild_id = memory_changes.guild_id AND m.campaign_id = memory_changes.campaign_id"
    " AND (m.right_by = dmbot_current_user() OR m.use_by = dmbot_current_user())"
)

SQL = (
    r"""
    DO $do$
    DECLARE
        c RECORD;
    BEGIN
        FOR c IN
            SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS def
              FROM pg_constraint con
              JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
             WHERE con.conrelid = 'campaign_maps'::regclass AND con.contype = 'c'
               AND a.attname = 'use_by' AND array_length(con.conkey, 1) = 1
               AND pg_get_constraintdef(con.oid) ~ '> 0'
        LOOP
            EXECUTE format('ALTER TABLE campaign_maps DROP CONSTRAINT %I', c.name);
            EXECUTE format('ALTER TABLE campaign_maps ADD CONSTRAINT %I %s', c.name,
                           replace(c.def, '> 0', '>= 0'));
        END LOOP;
    END
    $do$;
    DO $do$
    BEGIN
"""
    + f"""
        EXECUTE format('CREATE POLICY forget_maps_read ON campaign_maps FOR SELECT TO %I
            USING ({_GATE} AND (right_by = dmbot_current_user()
                                OR use_by = dmbot_current_user() OR use_by = 0))', current_user);
        EXECUTE format('CREATE POLICY forget_maps_delete ON campaign_maps FOR DELETE TO %I
            USING ({_GATE} AND right_by = dmbot_current_user())', current_user);
        EXECUTE format('CREATE POLICY forget_maps_update ON campaign_maps FOR UPDATE TO %I
            USING ({_GATE} AND use_by = dmbot_current_user())
            WITH CHECK ({_GATE} AND use_by = 0)', current_user);
        EXECUTE format('CREATE POLICY forget_maps_log_read ON memory_changes FOR SELECT TO %I
            USING ({_GATE} AND table_name = ''campaign_maps'' AND row_id IN (
                SELECT m.id FROM campaign_maps m WHERE {_MINE_MAP}))', current_user);
        EXECUTE format('CREATE POLICY forget_maps_log_delete ON memory_changes FOR DELETE TO %I
            USING ({_GATE} AND table_name = ''campaign_maps'' AND row_id IN (
                SELECT m.id FROM campaign_maps m WHERE {_MINE_MAP}))', current_user);
"""
    + r"""
    END
    $do$;
    CREATE FUNCTION dmbot_forget_maps() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_maps', true);
        -- the log rows first: they are found through the maps, which must still be there
        DELETE FROM memory_changes WHERE table_name = 'campaign_maps';
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

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (("dmbot_forget_maps", "dmbot_forget_maps()"),)
