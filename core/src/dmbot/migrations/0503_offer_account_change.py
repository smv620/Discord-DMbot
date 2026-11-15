"""0503_offer_account_change

A hand-over offer remembers whose accounts it was made between, so it can be closed, with a
notice, when one of them changes (#2533, slice 3).
"""

from __future__ import annotations

# An account can gain or lose a Discord login (0392). An offer made to a login that then joins
# another account, or by a login that is taken off its account, would otherwise sit open and be
# answered by someone it was not made for. Three new columns, all nullable (no old row changes,
# so no row-level security opens):
#   from_account, to_account  the accounts the two people were when the offer was made (the same
#                             number `dmbot_account_of` gives; a person with no account is their
#                             own number). NULL on an offer made before this, or by email: those
#                             are never closed for an account change.
#   closed_side               which side changed: 'offerer' (the DM's account), 'receiver' (the
#                             person offered to), or 'same' (both ended up on one account).
# and the status 'closed' joins the list. The bot closes the offers whose accounts differ now
# (`CampaignStore.close_changed_offers`, once a sweep) and tells both people once, in the words
# Supervisor ruled on #2533. The status list is read back from the database and 'closed' put in
# beside 'expired', found exactly once, so a drift stops the migration.
SQL = r"""
    ALTER TABLE campaign_handover_offers
        ADD COLUMN from_account BIGINT,
        ADD COLUMN to_account BIGINT,
        ADD COLUMN closed_side TEXT
            CHECK (closed_side IN ('offerer', 'receiver', 'same'));

    DO $do$
    DECLARE
        name text;
        d text;
    BEGIN
        SELECT c.conname, pg_get_constraintdef(c.oid) INTO name, d FROM pg_constraint c
            WHERE c.conrelid = 'campaign_handover_offers'::regclass AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) LIKE '%''expired''%';
        IF name IS NULL
           OR (length(d) - length(replace(d, '''expired''::text', '')))
              <> length('''expired''::text') THEN
            RAISE EXCEPTION 'the offer status list did not have the expected shape';
        END IF;
        EXECUTE format('ALTER TABLE campaign_handover_offers DROP CONSTRAINT %I', name);
        EXECUTE 'ALTER TABLE campaign_handover_offers ADD CONSTRAINT ' || quote_ident(name)
            || ' ' || replace(d, '''expired''::text', '''expired''::text, ''closed''::text');
    END
    $do$;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
