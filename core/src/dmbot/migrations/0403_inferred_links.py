"""0403_inferred_links

A connection DMbot inferred from lore, with its reason (#2959 item 5).
"""

from __future__ import annotations

# Supervisor's ruling on #2959 slice 2 (2026-11-09): the lore read may suggest a relationship the
# text implies but doesn't state, marked as DMbot's inference with a short reason. It waits like
# any connection and is never kept without the DM's press. `inferred_reason` is "" for a stated
# connection, so the existing rows need no change; it rides with the waiting row and, once the DM
# keeps it, with the kept connection's source (`memory_link_sources`), so the DM's "Why?" says
# DMbot inferred it. The reason is DMbot's own words about the DM's text: DM-only, like the label.
#
# The website does not show the mark yet (#3928), so until it does an inference must never read
# there as the DM's own text: the waiting list leaves inferred rows out (the DM answers them in
# Discord, where the card says "Inferred by DMbot"), and the line panel's "Why?" gets no quote for
# a kept one, only `inferred`, its reason. Both doors are read back as they are and each anchor
# must be found the exact number of times (the 0376 / 0400 way), so a drift stops the migration.
_WAITING_SWAPS = (
    (
        "ORDER BY b.created_at, b.id LIMIT 300",
        "AND b.inferred_reason = '' ORDER BY b.created_at, b.id LIMIT 300",
        1,
    ),
    (
        "WHERE b.guild_id = g AND b.campaign_id = c))",
        "WHERE b.guild_id = g AND b.campaign_id = c AND b.inferred_reason = ''))",
        1,
    ),
)
_LINK_SOURCE_SWAPS = (
    (
        "'quote', CASE WHEN is_dm THEN s.quote END,",
        "'quote', CASE WHEN is_dm AND s.inferred_reason = '' THEN s.quote END,"
        " 'inferred', CASE WHEN is_dm THEN NULLIF(s.inferred_reason, '') END,",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _swap(function: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    """One DO block: `function` read back as it is, each swap made, and made again."""
    steps = "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + function
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
"""
    )


SQL = (
    r"""
    ALTER TABLE backstory_links ADD COLUMN inferred_reason TEXT NOT NULL DEFAULT ''
        CONSTRAINT backstory_links_inferred_reason_check
        CHECK (char_length(inferred_reason) <= 80);
    ALTER TABLE memory_link_sources ADD COLUMN inferred_reason TEXT NOT NULL DEFAULT ''
        CONSTRAINT memory_link_sources_inferred_reason_check
        CHECK (char_length(inferred_reason) <= 80);
    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
"""
    + _swap("dmbot_web_waiting", _WAITING_SWAPS)
    + _swap("dmbot_web_link_sources", _LINK_SOURCE_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
