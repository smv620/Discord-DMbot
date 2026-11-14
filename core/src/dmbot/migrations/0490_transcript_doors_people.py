"""0490_transcript_doors_people

The transcript doors that return words check the campaign's-people rule themselves (#4404,
defence in depth for #4401), and the rule counts every Discord id of an account.
"""

from __future__ import annotations

# The website's pages already ask `dmbot_web_reads_campaign` before they call a transcript door
# (`web/transcripts.py`, #4402). This puts the same test inside the doors that hand over a
# transcript's words or its speakers, so a route added later that calls one of them directly cannot
# bypass the rule (#3891). The guard goes first in each door and answers NULL as a stranger gets.
# The doors that return only a session's number, date and length are left open on purpose:
# the recap and timeline pages, the pictures and the reports number their sessions from them.
# `dmbot_web_transcript_choose` and `dmbot_web_theatre_script` already answer a DM only.
# The doors are read back as they are, and each anchor must be found exactly once, so a drift stops
# the migration instead of changing the wrong line.
#
# The rule is also widened to what Discord does: a person may have added Discord ids to their
# account (0392), and a character they play, a table they spoke at or a by-hand list entry under
# any of those ids counts (`dmbot_account_logins`, the ids of the signed-in account). The website
# session is the account's own id, so before this only that id counted. A DM is unaffected
# (`campaign_dms` holds the account). No row is changed.
DOORS = (
    "dmbot_web_transcript_lines",
    "dmbot_web_transcript_characters",
    "dmbot_web_transcript_speakers",
    "dmbot_web_transcript_choices",
    "dmbot_web_transcript_stretch",
)
_OLD = "IF dmbot_current_guild() IS DISTINCT FROM g"
_NEW = (
    "IF NOT dmbot_web_reads_campaign(g, c)\n           OR dmbot_current_guild() IS DISTINCT FROM g"
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _guard(door: str) -> str:
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + door
        + "';\n        def := pg_temp.swap_n(def, "
        + _dollar(_OLD)
        + ", "
        + _dollar(_NEW)
        + r""", 1);
        EXECUTE def;
    END
    $do$;
"""
    )


SQL = r"""
    CREATE OR REPLACE FUNCTION dmbot_web_reads_campaign(g BIGINT, c TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ids BIGINT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN FALSE;
        END IF;
        IF EXISTS (SELECT 1 FROM campaign_dms d
                   WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me)
           OR EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c
                      AND k.channels_visibility = 'everyone') THEN
            RETURN TRUE;
        END IF;
        -- every Discord id that acts as this account: its own number unless the account took it off
        -- itself (0392), and its added ids. Never the number again after that.
        ids := ARRAY(SELECT dmbot_account_logins(me));
        RETURN EXISTS (SELECT 1 FROM campaign_channel_people x
                       WHERE x.guild_id = g AND x.campaign_id = c AND x.user_id = ANY (ids))
            OR EXISTS (SELECT 1 FROM memory_entities e
                       WHERE e.guild_id = g AND e.campaign_id = c
                         AND e.played_by = ANY (ids) AND e.type = 'character'
                         AND e.status = 'confirmed')
            OR EXISTS (SELECT 1 FROM transcript_lines l
                       JOIN transcript_sessions s ON s.id = l.session_id AND s.guild_id = l.guild_id
                       WHERE l.guild_id = g AND s.campaign_id = c AND l.sidebar IS NULL
                         AND NOT l.typed AND l.user_id = ANY (ids));
    END
    $fn$;

    -- (CREATE OR REPLACE drops a function's own settings, so they are set again)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_reads_campaign(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
""" + "".join(_guard(d) for d in DOORS)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
