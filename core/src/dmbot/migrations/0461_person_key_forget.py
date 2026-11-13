"""0461_person_key_forget

Slice d of #4145: after an account is deleted, the three records kept on purpose
(`try_it_used`, `trial_extensions`, `email_unsubscribed`) hold the keyed hash of the Discord ID
and no longer the ID.

- `user_id` may be empty in those three tables. Their keys on it become partial unique
  indexes (only where it is set), and a row must have an ID or a key.
- `dmbot_forget_person_ids()`, called by account deletion: sets `user_id` to NULL on this person's
  rows that already have a key. A row with no key (the secret was never set) keeps its ID, as
  before: clearing it would leave nothing to recognise the person by.
- `dmbot_move_account` takes the old and new accounts' keys (the website computes them), checks
  their shape, and writes them with the trial marker and the copied unsubscribe.

`sent_emails` is unchanged: its rows for the person are deleted at account deletion (0071), so
there is no ID left in it.
"""

from __future__ import annotations

_GATE = "dmbot_owner_sync() = ''person_ids''"

_OLD_HEAD = "(old bigint, new bigint, now bigint, which text)"
_NEW_HEAD = (
    "(old bigint, new bigint, now bigint, which text,"
    " old_key text DEFAULT NULL, new_key text DEFAULT NULL)"
)
_OLD_START = "PERFORM set_config('dmbot.owner_sync', 'move', true);\n        IF old IS NULL"
_NEW_START = (
    "PERFORM set_config('dmbot.owner_sync', 'move', true);\n"
    "        IF (old_key IS NOT NULL AND old_key !~ '^[0-9a-f]{64}$')\n"
    "           OR (new_key IS NOT NULL AND new_key !~ '^[0-9a-f]{64}$') THEN\n"
    "            PERFORM set_config('dmbot.owner_sync', before, true);\n"
    "            RETURN 'bad_key';\n"
    "        END IF;\n"
    "        IF old IS NULL"
)
_OLD_TRIAL = "INSERT INTO try_it_used (user_id) VALUES (old), (new) ON CONFLICT DO NOTHING;"
_NEW_TRIAL = (
    "INSERT INTO try_it_used (user_id, person_key) VALUES (old, old_key), (new, new_key)\n"
    "            ON CONFLICT DO NOTHING;"
)
_OLD_UNSUB = (
    "INSERT INTO email_unsubscribed (user_id, at)\n"
    "            SELECT new, at FROM email_unsubscribed WHERE user_id = old\n"
    "            ON CONFLICT DO NOTHING;"
)
_NEW_UNSUB = (
    "INSERT INTO email_unsubscribed (user_id, at, person_key)\n"
    "            SELECT new, at, new_key FROM email_unsubscribed\n"
    "             WHERE user_id = old OR (old_key IS NOT NULL AND person_key = old_key)\n"
    "            ON CONFLICT DO NOTHING;"
)

SQL = (
    r"""
    -- user_id may now be empty in the three kept tables; a row needs an ID or a key.
    ALTER TABLE try_it_used DROP CONSTRAINT try_it_used_pkey;
    ALTER TABLE try_it_used ALTER COLUMN user_id DROP NOT NULL;
    CREATE UNIQUE INDEX try_it_used_by_id ON try_it_used (user_id) WHERE user_id IS NOT NULL;
    ALTER TABLE try_it_used ADD CONSTRAINT try_it_used_who
        CHECK (user_id IS NOT NULL OR person_key IS NOT NULL);

    ALTER TABLE email_unsubscribed DROP CONSTRAINT email_unsubscribed_pkey;
    ALTER TABLE email_unsubscribed ALTER COLUMN user_id DROP NOT NULL;
    CREATE UNIQUE INDEX email_unsubscribed_by_id ON email_unsubscribed (user_id)
        WHERE user_id IS NOT NULL;
    ALTER TABLE email_unsubscribed ADD CONSTRAINT email_unsubscribed_who
        CHECK (user_id IS NOT NULL OR person_key IS NOT NULL);

    ALTER TABLE trial_extensions DROP CONSTRAINT trial_extensions_pkey;
    ALTER TABLE trial_extensions ALTER COLUMN user_id DROP NOT NULL;
    CREATE UNIQUE INDEX trial_extensions_by_id ON trial_extensions (user_id, extended_at)
        WHERE user_id IS NOT NULL;
    ALTER TABLE trial_extensions ADD CONSTRAINT trial_extensions_who
        CHECK (user_id IS NOT NULL OR person_key IS NOT NULL);

    -- (The read policy also shows already-cleared rows: an UPDATE's new row must pass the
    -- SELECT policy too, and a cleared row has no ID to match.)
    -- Only try_it_used is held by row-level security: the door reads and changes the person's
    -- own row through a gate that is open only while it runs.
    DO $do$
    BEGIN
        EXECUTE format('CREATE POLICY person_ids_read ON try_it_used FOR SELECT TO %I
            USING ("""
    + _GATE
    + r""" AND (user_id = dmbot_current_user() OR user_id IS NULL))', current_user);
        EXECUTE format('CREATE POLICY person_ids_clear ON try_it_used FOR UPDATE TO %I
            USING ("""
    + _GATE
    + r""" AND user_id = dmbot_current_user())
            WITH CHECK ("""
    + _GATE
    + r""" AND user_id IS NULL AND person_key IS NOT NULL)', current_user);
    END
    $do$;

    CREATE FUNCTION dmbot_forget_person_ids() RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        me BIGINT := dmbot_current_user();
        changed INTEGER;
        total INTEGER := 0;
    BEGIN
        IF me IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'person_ids', true);
        UPDATE try_it_used SET user_id = NULL WHERE user_id = me AND person_key IS NOT NULL;
        GET DIAGNOSTICS changed = ROW_COUNT;
        total := total + changed;
        UPDATE trial_extensions SET user_id = NULL WHERE user_id = me AND person_key IS NOT NULL;
        GET DIAGNOSTICS changed = ROW_COUNT;
        total := total + changed;
        UPDATE email_unsubscribed SET user_id = NULL
            WHERE user_id = me AND person_key IS NOT NULL;
        GET DIAGNOSTICS changed = ROW_COUNT;
        total := total + changed;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN total;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_person_ids() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_person_ids() FROM PUBLIC;

    -- The move door takes the keys: the old function is read back, edited in four places that
    -- must each be found exactly once (a drift stops the migration), and made again with the
    -- two new arguments.
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer)
        RETURNS text LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_move_account';
        def := pg_temp.swap_n(def, $q$"""
    + _OLD_HEAD
    + r"""$q$, $q$"""
    + _NEW_HEAD
    + r"""$q$, 1);
        def := pg_temp.swap_n(def, $q$"""
    + _OLD_START
    + r"""$q$, $q$"""
    + _NEW_START
    + r"""$q$, 1);
        def := pg_temp.swap_n(def, $q$"""
    + _OLD_TRIAL
    + r"""$q$, $q$"""
    + _NEW_TRIAL
    + r"""$q$, 1);
        def := pg_temp.swap_n(def, $q$"""
    + _OLD_UNSUB
    + r"""$q$, $q$"""
    + _NEW_UNSUB
    + r"""$q$, 1);
        DROP FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT);
        EXECUTE def;
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT)
        FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

# Account deletion is done on the website, as the person (like the other forget functions); the move
# door is the bot's.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_forget_person_ids", "dmbot_forget_person_ids()"),
)
