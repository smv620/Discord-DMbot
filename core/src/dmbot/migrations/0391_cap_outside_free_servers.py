"""0391_cap_outside_free_servers

The campaign cap leaves campaigns in free servers out (#3792).
"""

from __future__ import annotations

# A campaign in a `DMBOT_FREE_GUILDS` server has free access whoever its DM is (#3788), so it must
# neither use a slot of its owner's paid cap nor be the one that gets paused when the owner is over
# it. `owner_campaigns` deliberately holds no server, so both functions below look the server up
# in `campaigns` the way 0037's pause function already does: by opening `campaigns` to themselves
# for this one owner through the `pause` setting, which they set and put back. The server ids to
# leave out come from the caller (the configured list); an empty list is the old behaviour exactly.
#
# `dmbot_owned_campaigns_outside(skip)` is the count (the old no-argument one stays, for anything
# that has no list). `dmbot_pause_over_cap(keep, live, skip)` is 0037's function with the same
# list; the two-argument form is now a thin call to it with an empty list. No rows change.
SQL = r"""
    CREATE FUNCTION dmbot_owned_campaigns_outside(skip BIGINT[]) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n BIGINT;
    BEGIN
        IF me IS NULL THEN
            RETURN 0;
        END IF;
        IF COALESCE(cardinality(skip), 0) = 0 THEN
            RETURN (SELECT count(*) FROM owner_campaigns oc
                    WHERE oc.owner_user_id = me AND NOT oc.paused);
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'pause', true);
        SELECT count(*) INTO n FROM owner_campaigns oc
            WHERE oc.owner_user_id = me AND NOT oc.paused
              AND NOT EXISTS (SELECT 1 FROM campaigns c
                              WHERE c.id = oc.campaign_id AND c.owner_user_id = me
                                AND c.guild_id = ANY (skip));
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN n;
    END
    $fn$;

    CREATE FUNCTION dmbot_pause_over_cap(keep INTEGER, live TEXT[], skip BIGINT[])
        RETURNS TABLE (campaign_id TEXT, server_id BIGINT, campaign_name TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n BIGINT;
        left_out BIGINT[] := COALESCE(skip, '{}');
    BEGIN
        IF me IS NULL OR keep IS NULL OR keep < 0 THEN
            RETURN;
        END IF;
        n := dmbot_owned_campaigns_outside(left_out);
        IF n <= keep THEN
            RETURN;  -- nothing over the cap: the common case, answered before any lock
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'pause', true);
        -- Same lock order as 0037: the campaign rows first, in a fixed order, then the person.
        PERFORM 1 FROM campaigns c WHERE c.owner_user_id = me
            ORDER BY c.guild_id, c.id FOR UPDATE;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.owner_slots:' || me::text, 0));
        RETURN QUERY
        WITH ranked AS (
            SELECT c.id AS cid, c.guild_id AS gid,
                   row_number() OVER (
                       ORDER BY (c.id = ANY(live)) DESC,
                                COALESCE(c.last_played_at, c.created_at) DESC,
                                c.created_at DESC, c.id) AS n
            FROM campaigns c
            WHERE c.owner_user_id = me AND NOT c.paused AND NOT (c.guild_id = ANY (left_out))
        ), gone AS (
            UPDATE campaigns c SET paused = TRUE
            FROM ranked r
            WHERE c.id = r.cid AND c.guild_id = r.gid AND r.n > keep
            RETURNING c.id, c.guild_id, c.name
        )
        SELECT gone.id, gone.guild_id, gone.name FROM gone ORDER BY gone.name;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_pause_over_cap(keep INTEGER, live TEXT[] DEFAULT '{}')
        RETURNS TABLE (campaign_id TEXT, server_id BIGINT, campaign_name TEXT)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        SELECT * FROM dmbot_pause_over_cap(keep, live, '{}'::BIGINT[])
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_owned_campaigns_outside(BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[], BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_owned_campaigns_outside(BIGINT[]) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[], BIGINT[]) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[]) FROM PUBLIC;
"""

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The website's accept of a hand-over counts too, with the same list left out (0034).
    ("dmbot_owned_campaigns_outside", "dmbot_owned_campaigns_outside(BIGINT[])"),
)
