"""0526_waiting_recorded_imports

The website's waiting list counts an import's confirmed speakers and speakers without Discord as
recorded, as Discord does (#4603).
"""

from __future__ import annotations

# `dmbot_web_waiting` (0341) kept a claim only while every speaker it quotes has a `consent` row
# under the current wording. Discord counts two more kinds of people as recorded
# (`consent.still_recorded`): someone an import's uploader confirmed for this campaign (not taken
# back, and with no "no" on record), and a speaker without Discord (a negative id, which is never
# in `consent`, so a missing row must not read as a stop for them). So an imported session's items
# waited on the website while Discord showed them. It hid too much, never too little: no leak.
# This is the same repair 0518 made for `dmbot_web_threads` (#4596), with the same expression,
# per speaker: the consent test of 0341 (its `terms` and `outside` arguments are kept as they
# are), or an import confirmation of this campaign. The door is read back from the database as it
# is, and the anchor must be found exactly twice (the claims list and the totals), so a drift stops
# the migration, as 0341 does. Its signature is unchanged, so its pinned search path and the
# revoked PUBLIC grant stay. No row is changed.
_OLD = (
    "AND k.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n "
    "WHERE n.guild_id = g AND n.terms_version = terms "
    "AND (outside IS NULL OR n.outside_to = outside))"
)
_NEW = (
    "AND NOT EXISTS (SELECT 1 FROM unnest(k.speaker_ids) AS u(id) WHERE u.id > 0 AND NOT ("
    "EXISTS (SELECT 1 FROM consent n WHERE n.guild_id = g AND n.user_id = u.id "
    "AND n.terms_version = terms AND (outside IS NULL OR n.outside_to = outside)) "
    "OR (EXISTS (SELECT 1 FROM import_consents i WHERE i.guild_id = g AND i.campaign_id = c "
    "AND i.user_id = u.id AND i.removed_at IS NULL) "
    "AND NOT EXISTS (SELECT 1 FROM consent_refusals r "
    "WHERE r.guild_id = g AND r.user_id = u.id))))"
)

SQL = (
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
        def := pg_temp.swap_n(def, $o${_OLD}$o$, $n${_NEW}$n$, 2);"""
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

# The function keeps its signature, so it is already registered by 0341; nothing new here.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
