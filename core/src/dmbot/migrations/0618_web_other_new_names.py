"""0618_web_other_new_names

The website's list of names to check also shows the names no recap has linked (#5186).
"""

from __future__ import annotations

# `dmbot_campaign_name_checks` (0339) listed a campaign's recaps that have names waiting, joined to
# a recap that exists, so a proposed name no recap ever linked (found in the lore or a typed
# import, say) was counted on the Questions page and could not be opened from it. The bot now keeps
# such names at session 0 of `recap_name_checks` ("no recap"; `AnswerStore.link_orphans`), and the
# door lists that group too, last, without needing a recap row: every other door of the recap's
# list already answers by the link, so the same list, card and answers work for it. Read back from
# the database as it is; both anchors must be found exactly once, so a door that has drifted stops
# the migration instead of being rewritten from old text. Nothing is written.
_JOIN = (
    "                JOIN recap_jobs j\n"
    "                  ON j.guild_id = k.guild_id AND j.campaign_id = k.campaign_id\n"
    "                 AND j.session_at = k.session_at\n"
)
_LEFT = _JOIN.replace("JOIN recap_jobs j", "LEFT JOIN recap_jobs j", 1)
_WHERE = "                  AND e.status = 'proposed'\n"
_WHERE_NEW = _WHERE + "                  AND (j.session_at IS NOT NULL OR k.session_at = 0)\n"


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


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
              AND p.proname = 'dmbot_campaign_name_checks';
        def := pg_temp.swap_n(def, """
    + _dollar(_JOIN)
    + ", "
    + _dollar(_LEFT)
    + r""", 1);
        def := pg_temp.swap_n(def, """
    + _dollar(_WHERE)
    + ", "
    + _dollar(_WHERE_NEW)
    + r""", 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
