"""0155_support_ticket_times

No two of a person's tickets share a time.
"""

from __future__ import annotations

SUPPORT_TICKET_TIMES = """
    -- A person's tickets never share a sent time (#2214): the account page opens a ticket by that
    -- time (#2193). `support.add` stamps the next free second; this index is what holds two sends
    -- in the same instant apart, whichever commits second retries.
    -- Tickets already sharing a time are moved apart first: each pass moves every ticket that
    -- shares its second with an earlier id up by one, until none do. A ticket that was alone can
    -- be pushed too when the ones below it run into it, so a cluster of k ends up at most k-1
    -- seconds late; the order by id is kept. (The table has forced row-level security, so the
    -- migration opens it for itself and closes it again, as the other backfills do.) The older
    -- index (user_id, created_at DESC) is left as it is: dropping an index is not this change.
    CREATE POLICY migrate_backfill ON support_tickets FOR ALL USING (true) WITH CHECK (true);
    DO $do$
    BEGIN
        LOOP
            UPDATE support_tickets t SET created_at = t.created_at + 1
            FROM (SELECT id, row_number() OVER (PARTITION BY user_id, created_at ORDER BY id) AS n
                  FROM support_tickets) d
            WHERE t.id = d.id AND d.n > 1;
            EXIT WHEN NOT FOUND;
        END LOOP;
    END
    $do$;
    DROP POLICY migrate_backfill ON support_tickets;
    CREATE UNIQUE INDEX support_tickets_one_time ON support_tickets (user_id, created_at);
    """

SQL = SUPPORT_TICKET_TIMES
