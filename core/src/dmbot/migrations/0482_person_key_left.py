"""0482_person_key_left

How many rows still have no keyed hash, per table (#4383), so the person-key job can say so in its
log on every pass: a pass that found nothing to do was silent, and a table it could not reach
(34 `sent_emails` rows on deploy 26) went unseen. Counts only: no ID, key or address leaves it.
"""

from __future__ import annotations

# Read-only, for the function owner, open only while it runs under the same gate as the fill
# (0460: `person_key_fill`, whose read policy on `try_it_used` already exists). The other three
# tables have no row-level security.
SQL = r"""
    CREATE FUNCTION dmbot_person_key_left() RETURNS TABLE (tbl TEXT, n BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'person_key_fill', true);
        RETURN QUERY SELECT 'try_it_used'::TEXT, count(*) FROM try_it_used
            WHERE person_key IS NULL AND user_id IS NOT NULL;
        RETURN QUERY SELECT 'trial_extensions'::TEXT, count(*) FROM trial_extensions
            WHERE person_key IS NULL AND user_id IS NOT NULL;
        RETURN QUERY SELECT 'sent_emails'::TEXT, count(*) FROM sent_emails
            WHERE person_key IS NULL;
        RETURN QUERY SELECT 'email_unsubscribed'::TEXT, count(*) FROM email_unsubscribed
            WHERE person_key IS NULL AND user_id IS NOT NULL;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_person_key_left() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_person_key_left() FROM PUBLIC;
"""
