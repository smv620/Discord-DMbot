"""0460_person_key_backfill

Giving the rows written before the secret was set their keyed hash (#4145, slice c). The key is made
in Python (`dmbot.person_key`) and the database cannot compute it, so a bot job lists the Discord
IDs that have no key yet, hashes them, and hands the pairs back. Two doors, for the function owner
only and open only while they run (the same gate pattern as 0431 and 0440), because `try_it_used`
is held by row-level security and nothing else may read or change a person's rows here.
"""

from __future__ import annotations

_GATE = "dmbot_owner_sync() = ''person_key_fill''"


def _policies() -> str:
    # Only try_it_used has row-level security; the other three tables are the owner's to change.
    return f"""
    DO $do$
    BEGIN
        EXECUTE format('CREATE POLICY person_key_read ON try_it_used FOR SELECT TO %I
            USING ({_GATE})', current_user);
        EXECUTE format('CREATE POLICY person_key_fill ON try_it_used FOR UPDATE TO %I
            USING ({_GATE}) WITH CHECK ({_GATE})', current_user);
    END
    $do$;
"""


def _fill() -> str:
    # Each table: set the key where there is none, unless that key is already on another row (the
    # same person under another ID, or the same kind of email), which a unique index would refuse.
    # Such a row keeps no key and the job counts it as skipped.
    rows = {
        "try_it_used": "NOT EXISTS (SELECT 1 FROM try_it_used o WHERE o.person_key = k.key)",
        "email_unsubscribed": (
            "NOT EXISTS (SELECT 1 FROM email_unsubscribed o WHERE o.person_key = k.key)"
        ),
        "sent_emails": (
            "NOT EXISTS (SELECT 1 FROM sent_emails o"
            " WHERE o.person_key = k.key AND o.kind = t.kind)"
        ),
        "trial_extensions": "TRUE",
    }
    return "".join(
        f"""        UPDATE {table} t SET person_key = k.key
            FROM unnest(ids, keys) AS k(id, key)
            WHERE t.user_id = k.id AND t.person_key IS NULL AND {guard};
        GET DIAGNOSTICS changed = ROW_COUNT;
        total := total + changed;
"""
        for table, guard in rows.items()
    )


SQL = (
    _policies()
    + r"""
    -- The next IDs after `after` that have a row with no key, lowest first (keyset paging, so a row
    -- the fill skips cannot make the job loop). IDs only. Each table is cut at `batch` in ID order
    -- before the merge, so a page costs about a batch, not everything that is left.
    CREATE FUNCTION dmbot_person_key_todo(after BIGINT, batch INTEGER) RETURNS BIGINT[]
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT[];
    BEGIN
        IF after IS NULL OR batch IS NULL OR batch NOT BETWEEN 1 AND 1000 THEN
            RETURN ARRAY[]::BIGINT[];
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'person_key_fill', true);
        SELECT COALESCE(array_agg(u.id ORDER BY u.id), ARRAY[]::BIGINT[]) INTO found FROM (
            SELECT DISTINCT x.user_id AS id FROM (
                (SELECT user_id FROM try_it_used WHERE person_key IS NULL AND user_id > after
                    ORDER BY user_id LIMIT batch)
                UNION ALL (SELECT user_id FROM trial_extensions
                    WHERE person_key IS NULL AND user_id > after ORDER BY user_id LIMIT batch)
                UNION ALL (SELECT user_id FROM sent_emails
                    WHERE person_key IS NULL AND user_id > after ORDER BY user_id LIMIT batch)
                UNION ALL (SELECT user_id FROM email_unsubscribed
                    WHERE person_key IS NULL AND user_id > after ORDER BY user_id LIMIT batch)
            ) x ORDER BY x.user_id LIMIT batch
        ) u;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;

    -- Give these IDs their keys (pairs by position). A malformed key or mismatched arrays change
    -- nothing. Returns how many rows got a key.
    CREATE FUNCTION dmbot_person_key_fill(ids BIGINT[], keys TEXT[]) RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        changed INTEGER;
        total INTEGER := 0;
    BEGIN
        IF ids IS NULL OR keys IS NULL OR cardinality(ids) <> cardinality(keys)
           OR cardinality(ids) > 1000
           OR EXISTS (SELECT 1 FROM unnest(keys) AS k(key)
                      WHERE key IS NULL OR key !~ '^[0-9a-f]{64}$')
           OR EXISTS (SELECT 1 FROM unnest(ids) AS i(id) WHERE id IS NULL OR id <= 0) THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'person_key_fill', true);
"""
    + _fill()
    + r"""        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN total;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_person_key_todo(BIGINT, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_person_key_fill(BIGINT[], TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_person_key_todo(BIGINT, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_person_key_fill(BIGINT[], TEXT[]) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

# The bot's own doors: the website's role has no part in the backfill.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
