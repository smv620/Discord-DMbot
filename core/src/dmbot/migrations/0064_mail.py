"""0064_mail."""

from __future__ import annotations

MAIL = """
    -- Sending email (#1229; docs/PLAN.md, "Accounts"). Two small tables of ids and times, never
    -- an address or any text:
    --   sent_emails         who was sent which kind of email and when, so a one-off email goes
    --                       once and the daily cap can be counted (a kind that may repeat has
    --                       a round number in its name)
    --   email_unsubscribed  people who asked for no offer emails; written by the website's
    --                       unsubscribe link (which proves it with a signed token) and read by
    --                       the sender
    -- Like the routing tables they belong to no server, and they are written by jobs with no
    -- person set, so there is no row-level security to open.
    CREATE TABLE sent_emails (
        user_id BIGINT NOT NULL CHECK (user_id > 0),
        kind    TEXT   NOT NULL CHECK (char_length(kind) BETWEEN 1 AND 60),
        sent_at BIGINT NOT NULL,
        PRIMARY KEY (user_id, kind)
    );
    CREATE INDEX sent_emails_by_day ON sent_emails (sent_at);
    CREATE TABLE email_unsubscribed (
        user_id BIGINT PRIMARY KEY CHECK (user_id > 0),
        at      BIGINT NOT NULL
    );
    -- The one door for sending an email, for the bot and the website alike (the website's
    -- role has no grant on sent_emails: the table is everyone's email history). Under one
    -- lock it counts today's rows, applies the caps the caller passes (never above 400 in
    -- all and 300 for offers, so the sign-in links and move codes people ask for always have
    -- room), writes the row (an email of this kind goes once) and answers with a status:
    -- 'sent' (go ahead), 'duplicate' (already sent), 'capped' (the day is full) or 'refused'
    -- (not an email kind). The day and the time are the database's own. A failed send keeps
    -- its row (at most once), except an offer, which the bot gives back with
    -- dmbot_release_email so one hiccup doesn't lose it. Kinds starting "dm:" are private
    -- message records, not emails, and are refused here.
    CREATE FUNCTION dmbot_claim_email(who BIGINT, what TEXT, offer BOOLEAN, cap INTEGER,
                                      offer_cap INTEGER) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        stamp BIGINT := extract(epoch FROM now())::BIGINT;
        today BIGINT;
        added INTEGER;
        most INTEGER := LEAST(COALESCE(cap, 400), 400);
        most_offers INTEGER := LEAST(COALESCE(offer_cap, 300), 300, most);
    BEGIN
        IF who IS NULL OR who <= 0 OR what IS NULL OR char_length(what) NOT BETWEEN 1 AND 60
           OR what LIKE 'dm:%' THEN
            RETURN 'refused';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.email_day', 0));
        SELECT count(*) INTO today FROM sent_emails s
            WHERE s.sent_at >= stamp - stamp % 86400 AND s.kind NOT LIKE 'dm:%';
        IF EXISTS (SELECT 1 FROM sent_emails s WHERE s.user_id = who AND s.kind = what) THEN
            RETURN 'duplicate';
        END IF;
        IF today >= most OR (offer AND today >= most_offers) THEN
            RETURN 'capped';
        END IF;
        INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (who, what, stamp)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        RETURN CASE WHEN added = 1 THEN 'sent' ELSE 'duplicate' END;
    END
    $fn$;
    -- Gives back the claim of an email that did not go (bot only: the website's role has no
    -- EXECUTE), so the next run tries again.
    CREATE FUNCTION dmbot_release_email(who BIGINT, what TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        DELETE FROM sent_emails s WHERE s.user_id = who AND s.kind = what;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_release_email(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_release_email(BIGINT, TEXT) FROM PUBLIC;
    """

SQL = MAIL

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_claim_email", "dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER)"),
)
