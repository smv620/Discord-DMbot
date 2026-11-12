"""0458_person_key_columns

A keyed hash of the Discord ID beside the ID in the four records that must outlive an account
(#4145, slice b): `try_it_used` (one free trial per person), `trial_extensions`, `sent_emails` and
`email_unsubscribed`. The hash is `dmbot.person_key` (HMAC-SHA256 under `DMBOT_PERSON_KEY`), made in
Python and handed in: the database never sees the secret and cannot compute it.

This slice only adds the column, its indexes and the doors that take the key; `user_id` stays as it
is. Rows written from now on carry both; a row written before has no key until the backfill job
(slice c) gives it one. Until the secret is set nothing passes a key and everything works as before.
Deleting an account nulling `user_id` (slice d) and `NOT NULL` on the key (slice e) come later.
"""

from __future__ import annotations

# A key is 64 lowercase hex characters or NULL (a row not yet given one). The partial unique indexes
# are what recognise a returning person: an INSERT that names a key already there conflicts, even
# when the old row can no longer be read by the session (`ON CONFLICT DO NOTHING` ignores row-level
# security, unlike DO UPDATE).
_KEY_OK = "person_key IS NULL OR person_key ~ '^[0-9a-f]{64}$'"

SQL = (
    f"""
    ALTER TABLE try_it_used ADD COLUMN person_key TEXT CHECK ({_KEY_OK});
    ALTER TABLE trial_extensions ADD COLUMN person_key TEXT CHECK ({_KEY_OK});
    ALTER TABLE sent_emails ADD COLUMN person_key TEXT CHECK ({_KEY_OK});
    ALTER TABLE email_unsubscribed ADD COLUMN person_key TEXT CHECK ({_KEY_OK});
    CREATE UNIQUE INDEX try_it_used_by_key ON try_it_used (person_key)
        WHERE person_key IS NOT NULL;
    CREATE UNIQUE INDEX email_unsubscribed_by_key ON email_unsubscribed (person_key)
        WHERE person_key IS NOT NULL;
    CREATE UNIQUE INDEX sent_emails_by_key ON sent_emails (person_key, kind)
        WHERE person_key IS NOT NULL;
    -- (not unique: two extensions in one second would make the door fail; the table's own key
    -- on user_id already keeps one per second, and a person is counted, not looked up, by it)
    CREATE INDEX trial_extensions_by_key ON trial_extensions (person_key)
        WHERE person_key IS NOT NULL;
"""
    + r"""
    -- The three doors that write these records take the key after their old arguments (NULL: no key
    -- yet, as before). A malformed key is refused, never stored. The old signatures are dropped:
    -- a call with the old arguments still works, through the default.
    DROP FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER);
    CREATE FUNCTION dmbot_claim_email(
        who BIGINT, what TEXT, offer BOOLEAN, cap INTEGER, offer_cap INTEGER,
        who_key TEXT DEFAULT NULL
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    #variable_conflict use_column
    DECLARE
        stamp BIGINT := extract(epoch FROM now())::BIGINT;
        today BIGINT;
        added INTEGER;
        most INTEGER := LEAST(COALESCE(cap, 400), 400);
        most_offers INTEGER := LEAST(COALESCE(offer_cap, 300), 300, most);
    BEGIN
        IF who IS NULL OR who <= 0 OR what IS NULL OR char_length(what) NOT BETWEEN 1 AND 60
           OR what LIKE 'dm:%'
           OR (who_key IS NOT NULL AND who_key !~ '^[0-9a-f]{64}$') THEN
            RETURN 'refused';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.email_day', 0));
        SELECT count(*) INTO today FROM sent_emails s
            WHERE s.sent_at >= stamp - stamp % 86400 AND s.kind NOT LIKE 'dm:%';
        IF EXISTS (SELECT 1 FROM sent_emails s WHERE s.kind = what
                   AND (s.user_id = who OR (who_key IS NOT NULL AND s.person_key = who_key))) THEN
            RETURN 'duplicate';
        END IF;
        IF today >= most OR (offer AND today >= most_offers) THEN
            RETURN 'capped';
        END IF;
        INSERT INTO sent_emails (user_id, kind, sent_at, person_key)
            VALUES (who, what, stamp, who_key)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        RETURN CASE WHEN added = 1 THEN 'sent' ELSE 'duplicate' END;
    END
    $fn$;

    DROP FUNCTION dmbot_release_email(BIGINT, TEXT);
    CREATE FUNCTION dmbot_release_email(who BIGINT, what TEXT, who_key TEXT DEFAULT NULL)
    RETURNS VOID LANGUAGE sql SECURITY DEFINER AS $fn$
        DELETE FROM sent_emails s WHERE s.kind = what
            AND (s.user_id = who OR (who_key IS NOT NULL AND s.person_key = who_key));
    $fn$;

    DROP FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER);
    CREATE FUNCTION dmbot_extend_trial(
        who BIGINT, now BIGINT, days INTEGER, who_key TEXT DEFAULT NULL
    ) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        used BIGINT;
        changed INTEGER;
    BEGIN
        IF who_key IS NOT NULL AND who_key !~ '^[0-9a-f]{64}$' THEN
            RETURN FALSE;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        -- Only a Try It that has ended and is still the person's plan, for 30 days, three
        -- days after the end, for someone who never paid and has used under 4 hours in all.
        IF days <> 30 THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN FALSE;
        END IF;
        SELECT COALESCE(sum(h.minutes), 0) INTO used FROM owner_hours h
            WHERE h.owner_user_id = who;
        UPDATE entitlements
           SET period_start = now, period_end = now + days * 86400, hours_cap = 8,
               minutes_taken = used, extra_hours = 0, plan_changed_at = now, updated_at = now
         WHERE entitlements.user_id = who AND plan = 'try-it' AND status = 'active'
           AND period_end + 259200 <= now AND used < 240
           AND NOT EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = who
                           AND (p.kind IS NULL OR p.kind = ANY(
                                ARRAY['subscription_started', 'subscription_renewed',
                                      'extra_hours_bought'])));
        GET DIAGNOSTICS changed = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        IF changed = 0 THEN
            RETURN FALSE;
        END IF;
        INSERT INTO trial_extensions (user_id, extended_at, person_key) VALUES (who, now, who_key);
        RETURN TRUE;
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_release_email(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER, TEXT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_release_email(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

# The website sends its own account emails through the claim door (0064), so the new signature is
# granted to its role; the other two are the bot's.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_claim_email",
        "dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER, TEXT)",
    ),
)
