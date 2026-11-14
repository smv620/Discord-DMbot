"""0489_reads_campaign

Who may read a campaign's transcripts, in one place (#4401; the owner's ruling of
2026-11-09, #3891).
"""

from __future__ import annotations

import importlib

# `dmbot_web_reads_campaign(g, c)`: whether the signed-in person may read this campaign's
# transcripts, and so see what is shown to its table. The campaign's people may: its DMs, a
# confirmed character's player, someone heard at one of its sessions, and anyone a DM added by
# hand (`campaign_channel_people`); and, only when the campaign's `channels_visibility` is
# 'everyone', any member of the server. This is `dm_screen.channel_sync.campaign_people`'s test,
# copied exactly, so Discord's channels and the website never differ. False for anyone who is not
# a member of the server, and for a campaign that is not there. The website's transcript pages
# ask it before they ask a transcript door (`web/transcripts.py`), and the Maps page's door
# (0487) now calls it instead of keeping a copy. It reads who someone is, never a line of a
# transcript.
#
# One difference from Discord's list stays, said here: a person who plays or spoke under a second
# Discord id they added to their account is matched there by every id, but a website session is the
# account's own id, so here only that id counts. (A DM is unaffected: `campaign_dms` holds the
# account.)
#
# 0487's own door is read back and its inline copy of the test is swapped for a call, each anchor
# found exactly once, so a drift stops the migration. No row is changed.
_MAPS = importlib.import_module("dmbot.migrations.0487_maps_doors")
_START = "reads := is_dm"
_END = "AND NOT l.typed AND l.user_id = dmbot_current_user());"
_SQL = str(_MAPS.SQL)
_BLOCK = _SQL[_SQL.index(_START) : _SQL.index(_END, _SQL.index(_START)) + len(_END)]

# The helper's own body is the same test, so it is written from the very text the maps door held.
_TEST = _BLOCK.replace("reads := is_dm", "RETURN is_dm", 1).rstrip(";")


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    CREATE FUNCTION dmbot_web_reads_campaign(g BIGINT, c TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN FALSE;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        """
    + _TEST
    + r""";
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_reads_campaign(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_reads_campaign(BIGINT, TEXT) FROM PUBLIC;

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
              AND p.proname = 'dmbot_web_maps';
        def := pg_temp.swap_n(def, """
    + _dollar(_BLOCK)
    + r""", 'reads := dmbot_web_reads_campaign(g, c);', 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_reads_campaign", "dmbot_web_reads_campaign(BIGINT, TEXT)"),
)
