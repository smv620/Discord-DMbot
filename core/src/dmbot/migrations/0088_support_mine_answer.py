"""0088_support_mine_answer

A person's own latest answer (#1432).
"""

from __future__ import annotations

SUPPORT_MINE_ANSWER = """
    -- A person's own latest answer, for the account page's "Your questions" (#1432): who answered
    -- (the helper, or the team) and the answer's words, so the page can show its first line.
    -- Only an answer (never the person's own emailed replies) and only on a ticket of the
    -- signed-in person (`dmbot_current_user()`, set by Database.user), never anyone else's. The
    -- website's role has no column right on a reply's words, so this door is the only way they
    -- come back, and the policy below lets the door (not the role's own queries, which have no
    -- right on the words) see a person's own answers. (The policy also lets a person's own session
    -- list the ids, kinds and times of their own answers, which the role may already read in
    -- those four columns: the answers themselves come to them by email, so nothing is exposed.)
    CREATE POLICY read_own_answers ON support_replies FOR SELECT
        USING (who IN ('ai', 'person') AND EXISTS (
            SELECT 1 FROM support_tickets t
            WHERE t.id = support_replies.ticket_id AND t.user_id = dmbot_current_user()));
    CREATE FUNCTION dmbot_support_latest_answer(ticket BIGINT)
        RETURNS TABLE (who TEXT, text TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        RETURN QUERY SELECT r.who, r.text FROM support_replies r
            WHERE r.ticket_id = ticket AND r.who IN ('ai', 'person')
              AND EXISTS (SELECT 1 FROM support_tickets t
                          WHERE t.id = ticket AND t.user_id = dmbot_current_user())
            ORDER BY r.id DESC LIMIT 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_support_latest_answer(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_support_latest_answer(BIGINT) FROM PUBLIC;
    """

SQL = SUPPORT_MINE_ANSWER

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A person's own latest answer, on their own ticket only (#1432).
    ("dmbot_support_latest_answer", "dmbot_support_latest_answer(BIGINT)"),
)
