"""0047_owner_left."""

from __future__ import annotations

OWNER_LEFT = """
    -- A campaign whose owner deleted their account is kept, ownerless (#1059; owner decision
    -- 2026-10-11, #922): the players may still want to read and download it. `owner_left_at`
    -- is when it lost its owner; the retention job deletes it 120 days later unless another DM
    -- takes it on (which clears this). NULL for a campaign that never had an owner, or has one.
    -- A column with no default: no rows change, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN owner_left_at BIGINT;

    -- Deleting an account must reach the person's campaigns in every server, which no
    -- server-scoped call can. Like the pause function (0037) this is the one door: it runs with
    -- its owner's rights, opens `campaigns` to itself for this one person through a setting it
    -- sets and puts back, can only turn that person's campaigns ownerless, and takes no person
    -- as an argument (it uses the one set for the transaction). Only the website's role calls it.
    -- (orphan_read also shows the row it has just made ownerless: an UPDATE's new row has to
    -- pass the SELECT rule too.)
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY orphan_read ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''orphan''
                       AND (owner_user_id = dmbot_current_user()
                            OR (owner_user_id IS NULL AND owner_left_at IS NOT NULL)))',
            current_user);
        EXECUTE format(
            'CREATE POLICY orphan_update ON campaigns FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''orphan'' AND owner_user_id = dmbot_current_user())
                WITH CHECK (dmbot_owner_sync() = ''orphan'' AND owner_user_id IS NULL
                            AND owner_left_at IS NOT NULL)',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_orphan_owned_campaigns(at BIGINT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n BIGINT;
    BEGIN
        IF me IS NULL OR at IS NULL OR at <= 0 THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'orphan', true);
        -- Campaign rows in a fixed order, like the pause function, so a hand-over or a pause
        -- running at the same time can't cross with this.
        PERFORM 1 FROM campaigns c WHERE c.owner_user_id = me
            ORDER BY c.guild_id, c.id FOR UPDATE;
        UPDATE campaigns SET owner_user_id = NULL, owner_left_at = at
            WHERE owner_user_id = me;
        GET DIAGNOSTICS n = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN n;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_orphan_owned_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_orphan_owned_campaigns(BIGINT) FROM PUBLIC;
"""

SQL = OWNER_LEFT

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_orphan_owned_campaigns", "dmbot_orphan_owned_campaigns(BIGINT)"),
)
