"""0150_support_mine_thread

A person reads their own ticket's thread (#2193).
"""

from __future__ import annotations

SUPPORT_MINE_THREAD = """
    -- A person reads the whole conversation of one of their own tickets (#2193; docs/PLAN.md,
    -- "Support flow"): their question, the helper's answers, their emailed replies and the team's
    -- replies, oldest first. Like the latest-answer door, it asks only for a ticket whose
    -- user_id is the signed-in person (`dmbot_current_user()`, set by Database.user), so another
    -- account's ticket and a missing one look the same (no rows). The forced `read_own` policy on
    -- support_tickets says the same thing a second time: two layers, either of which holds.
    -- The website's role still has no column right on any of the words: this door is the only
    -- way they come back.
    -- A ticket is named by its sent time because the list shows no ticket numbers (they would
    -- show how many other people write); a person's tickets never share a sent time (the insert
    -- stamps a second ticket one second later), and if one ever did the newer would be shown.
    -- The policy lets the door (not the role's own queries, which have no right on the words) see
    -- a person's own emailed replies too; the ids, kinds and times of those are the only new thing
    -- a person's own session could list, and they are their own messages.
    CREATE POLICY read_own_replies ON support_replies FOR SELECT
        USING (who = 'customer' AND EXISTS (
            SELECT 1 FROM support_tickets t
            WHERE t.id = support_replies.ticket_id AND t.user_id = dmbot_current_user()));
    CREATE FUNCTION dmbot_support_mine_thread(sent BIGINT)
        RETURNS TABLE (who TEXT, text TEXT, created_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        mine support_tickets%ROWTYPE;
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        SELECT * INTO mine FROM support_tickets t
            WHERE t.user_id = dmbot_current_user() AND t.created_at = sent
            ORDER BY t.id DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        RETURN QUERY
            SELECT o.who, o.text, o.created_at FROM (
                SELECT 0::BIGINT AS ord, 'customer'::TEXT AS who, mine.message AS text,
                       mine.created_at AS created_at
                UNION ALL
                SELECT r.id, r.who, r.text, r.created_at FROM support_replies r
                WHERE r.ticket_id = mine.id
            ) o ORDER BY o.ord;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_mine_thread(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_mine_thread(BIGINT) FROM PUBLIC;
    """

SQL = SUPPORT_MINE_THREAD

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A person's own whole conversation, on their own ticket only (#2193).
    ("dmbot_support_mine_thread", "dmbot_support_mine_thread(BIGINT)"),
)
