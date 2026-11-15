"""0497_forget_membership

Who is in a campaign, on account deletion (#4148; Supervisor's ruling on the issue, 2026-11-14):
`dmbot_forget_membership()`, called from `accounts.delete_person` after
`dmbot_forget_recap_approval()` (0495), on the pattern of 0440 and 0493.

- `campaign_dms`: the deleted account **always comes out of the DM list**, even when it is the
  campaign's only DM. `user_id` is part of the primary key, so setting it to 0 is not an option; the
  row goes. A campaign left with no DM is "owner left" (`dmbot_orphan_owned_campaigns`, 0047): its
  grace period and its backup download cover it, and every reader of the list copes with it empty
  (each is named in the pull request and tested).
- `campaign_handover_offers`: an **open** offer from or to the person is cancelled (deleted); an
  answered one keeps its row with the person's ID at 0 ("someone who has left") **and their display
  name on the offer (`from_name` / `to_name`, put there by 0022) blanked**, and an ended one still
  waiting to tell its owner (`end_told_at`) is marked told, so the end-notice sweep never tries to
  name a person who has left. The check
  `to_user_id <> from_user_id` is relaxed so that two people who have both left (both 0) can still
  stand in one row.
"""

from __future__ import annotations

# The person's own rows, deleted: (table, the column that holds the person's Discord ID).
DELETED: tuple[tuple[str, str], ...] = (("campaign_dms", "user_id"),)
# Kept with the ID at 0 (both columns, one UPDATE): the answered offers.
OFFERS = ("campaign_handover_offers", "from_user_id", "to_user_id")
ZEROED: tuple[tuple[str, str], ...] = ((OFFERS[0], OFFERS[1]), (OFFERS[0], OFFERS[2]))

# Every policy is for the function's owner only (`TO current_user`) and open only while the function
# runs (its own `dmbot.owner_sync` value, as 0425, 0430 and 0431 do).
_GATE = "dmbot_owner_sync() = ''forget_membership''"


def _policies() -> str:
    table, a, b = OFFERS
    out = ["    DO $do$\n    BEGIN\n"]
    for t, column in DELETED:
        out.append(
            f"        EXECUTE format('CREATE POLICY forget_membership_read ON {t} FOR SELECT"
            f" TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
            f"        EXECUTE format('CREATE POLICY forget_membership_delete ON {t} FOR DELETE"
            f" TO %I\n"
            f"            USING ({_GATE} AND {column} = dmbot_current_user())', current_user);\n"
        )
    mine = f"{a} = dmbot_current_user() OR {b} = dmbot_current_user()"
    out.append(
        f"        EXECUTE format('CREATE POLICY forget_membership_read ON {table} FOR SELECT"
        f" TO %I\n"
        f"            USING ({_GATE} AND ({mine} OR {a} = 0 OR {b} = 0))', current_user);\n"
        f"        EXECUTE format('CREATE POLICY forget_membership_delete ON {table} FOR DELETE"
        f" TO %I\n"
        f"            USING ({_GATE} AND status = ''open'' AND ({mine}))', current_user);\n"
        f"        EXECUTE format('CREATE POLICY forget_membership_update ON {table} FOR UPDATE"
        f" TO %I\n"
        f"            USING ({_GATE} AND ({mine}))\n"
        f"            WITH CHECK ({_GATE} AND {a} IS DISTINCT FROM dmbot_current_user()\n"
        f"                AND {b} IS DISTINCT FROM dmbot_current_user())', current_user);\n"
    )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _body() -> str:
    table, a, b = OFFERS
    deleted = "".join(
        f"        DELETE FROM {t} WHERE {c} = dmbot_current_user();\n" for t, c in DELETED
    )
    offers = f"""        -- (open offers are cancelled; answered ones keep nobody in their place)
        DELETE FROM {table} WHERE status = 'open'
            AND ({a} = dmbot_current_user() OR {b} = dmbot_current_user());
        UPDATE {table}
            SET end_told_at = COALESCE(end_told_at, extract(epoch FROM now())::BIGINT),
                from_name = CASE WHEN {a} = dmbot_current_user() THEN '' ELSE from_name END,
                to_name = CASE WHEN {b} = dmbot_current_user() THEN '' ELSE to_name END,
                {a} = CASE WHEN {a} = dmbot_current_user() THEN 0 ELSE {a} END,
                {b} = CASE WHEN {b} = dmbot_current_user() THEN 0 ELSE {b} END
            WHERE {a} = dmbot_current_user() OR {b} = dmbot_current_user();
"""
    return deleted + offers


SQL = (
    r"""
    -- Two people who have both left (0 and 0) can stand in one answered offer.
    ALTER TABLE campaign_handover_offers DROP CONSTRAINT campaign_handover_offers_check;
    ALTER TABLE campaign_handover_offers ADD CONSTRAINT campaign_handover_offers_check
        CHECK (to_user_id = 0 OR from_user_id = 0 OR to_user_id <> from_user_id);
"""
    + _policies()
    + r"""
    CREATE FUNCTION dmbot_forget_membership() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_membership', true);
"""
    + _body()
    + r"""        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_membership() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_membership() FROM PUBLIC;
"""
)

# Account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_membership", "dmbot_forget_membership()"),
)
