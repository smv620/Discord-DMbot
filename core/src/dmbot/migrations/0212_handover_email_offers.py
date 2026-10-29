"""0212_handover_email_offers

Offers sent by email (#2532).
"""

from __future__ import annotations

HANDOVER_EMAIL = """
    -- Hand-over by email (#2532, docs/PLAN.md, "Hand-over by email"): an offer is no longer made to
    -- a Discord account but sent to an email address, and the person who opens its one-use link
    -- on the website and accepts becomes the new owner. The address is never stored here (it goes
    -- in the email and nowhere else): the offer holds only a SHA-256 of the link's token. Until
    -- the link is used `to_user_id` is 0, which matches nobody; the website's claim sets it to
    -- the signed-in person, and the rest is the offer machinery of 0021 (one open offer per
    -- campaign, seven days, the owner told when it ends). A new column on an existing table: no
    -- rows change, so there is no backfill policy.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN via_email  BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN token_hash TEXT CHECK (token_hash IS NULL OR char_length(token_hash) = 64);
    CREATE UNIQUE INDEX campaign_handover_offers_token
        ON campaign_handover_offers (token_hash) WHERE token_hash IS NOT NULL;

    -- How many offers by email this person has sent in the last day, for the limit of five a
    -- day, counted over all their campaigns and servers (the offers table is scoped to one
    -- server at a time, so the count reads the mail log, which is everyone's). The caller must
    -- be that person, or a job with no person set.
    CREATE FUNCTION dmbot_handover_emails_since(p_user BIGINT, p_since BIGINT) RETURNS INTEGER
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_user() IS NOT NULL AND dmbot_current_user() <> p_user THEN
            RETURN 0;
        END IF;
        RETURN (SELECT count(*)::INTEGER FROM sent_emails e
                WHERE e.user_id = p_user AND e.kind LIKE 'handover:%' AND e.sent_at >= p_since);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_emails_since(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_emails_since(BIGINT, BIGINT) FROM PUBLIC;
"""

SQL = HANDOVER_EMAIL

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # How many hand-over emails a person sent today, for the limit of five (#2532).
    ("dmbot_handover_emails_since", "dmbot_handover_emails_since(BIGINT, BIGINT)"),
)
