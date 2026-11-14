"""0341_waiting_recorded_now

The website's waiting list keeps only claims whose every speaker is recorded now (#3466).
"""

from __future__ import annotations

# `dmbot_web_waiting` (0233 and its later changes) listed and counted every waiting claim that had a
# speaker, so it could show a claim built from the words of someone who has since stopped being
# recorded, which Discord's list (`ClaimStore.waiting`, `speaker_ids <@ opted_in`) leaves out. The
# same defect, and the same repair, as the readings door (0340, #3462): the claims list and the
# totals keep a claim only when every one of its speakers is recorded now by the bot's own rule
# (`consent._select`): a consent row under the current wording (`terms`) and, while an outside
# company hears the voices, for that company (`outside`). Both arrive as arguments because the
# database cannot know them, so the door has a new signature and the old one goes. A pause (off the
# record for the moment) lives in the bot's memory and a door cannot see it; Supervisor ruled that
# gap acceptable on #3464. The door is read back from the database as it is (several migrations
# have changed it), and each anchor must be found the exact number of times (a drift stops the
# migration), as 0332 does. Its pinned search path is kept by the definition itself; PUBLIC stays
# revoked. No row is changed.
_RECORDED = (
    "AND k.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n "
    "WHERE n.guild_id = g AND n.terms_version = terms "
    "AND (outside IS NULL OR n.outside_to = outside))"
)
_ANCHOR = "AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0"

_NEW_HEADER = (
    "CREATE OR REPLACE FUNCTION dmbot_web_waiting(g bigint, c text, terms integer, outside text)"
)

WAITING_RECORDED_NOW = (
    r"""
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
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
              AND p.proname = 'dmbot_web_waiting';
"""
    + f"""
        def := pg_temp.swap_n(
            def,
            'CREATE OR REPLACE FUNCTION ' || quote_ident(current_schema())
                || '.dmbot_web_waiting(g bigint, c text)',
            $n${_NEW_HEADER}$n$, 1);
        def := pg_temp.swap_n(def, $o${_ANCHOR}$o$, $n${_ANCHOR} {_RECORDED}$n$, 2);"""
    + r"""
        EXECUTE def;
        EXECUTE format('DROP FUNCTION %I.dmbot_web_waiting(BIGINT, TEXT)', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_waiting(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_waiting(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    """
)

SQL = WAITING_RECORDED_NOW

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_waiting", "dmbot_web_waiting(BIGINT, TEXT, INT, TEXT)"),
)
