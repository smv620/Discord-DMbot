"""0048_plan_ends."""

from __future__ import annotations

PLAN_ENDS = """
    -- A paid plan that its owner has cancelled, which keeps working to the end of the period
    -- already paid for (#824): the payment company's "cancelled" event stores that end here, and
    -- a renewal or a resume clears it. NULL while the plan will renew. A nullable column: no rows
    -- change, so no row-level-security policy is needed.
    ALTER TABLE entitlements ADD COLUMN ends_at BIGINT;

    -- The hours a person has used in one plan month, for the account page's hours bar. The
    -- website's role has no grant on owner_hours, and the table is scoped to the owner, so this
    -- is the one door: it takes no person (it uses the one set for the transaction, so it can't
    -- be pointed at anyone else) and returns one number, no server or campaign.
    CREATE FUNCTION dmbot_hours_used(month BIGINT) RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT COALESCE((SELECT minutes FROM owner_hours
            WHERE owner_user_id = dmbot_current_user() AND month_start = month), 0)::BIGINT
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_hours_used(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_hours_used(BIGINT) FROM PUBLIC;
"""

SQL = PLAN_ENDS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_hours_used", "dmbot_hours_used(BIGINT)"),
)
