"""0451_language_questions

A language the lore gives a character the campaign already has waits as a question for the DM, like
a lineage or a class (#4229 slice 1c).
"""

from __future__ import annotations

# Supervisor's ruling on #4229: a lore language for an existing character with no sheet is never
# written silently (#3926's rule for lineage and class). It waits in `memory_trait_offers` as one
# more kind, 'language': "Your lore says Brewner speaks Giant. Add it?" ✅ writes it as the DM's own
# word (`memory_story_languages`, given_by 'dm'); ❌ is final, as for the others. A language is not
# a rules link, so it has no source: `rules_source` is '' and `known` false, which the table's own
# check already holds to. No existing row changes, so no row-level security needs opening.
#
# The website's listing door (`dmbot_web_trait_offers`, 0410) gives only lineage and class
# questions until its page can word a language question (WebDev's half, filed on #4229), so the page
# is never shown a question it can't say. The answer door (0411) and `trait_answers` are unchanged:
# they take only 'species' and 'class' still. The door is read back from the database as it is and
# its anchor must be found exactly once, so a drift stops the migration (as 0437 does).
_LISTING_SWAPS = (
    (
        "                WHERE o.guild_id = g AND o.campaign_id = c AND o.state = 'waiting'\n",
        "                WHERE o.guild_id = g AND o.campaign_id = c AND o.state = 'waiting'\n"
        "                  AND o.kind IN ('species', 'class')\n",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
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
        + door
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = r"""
    ALTER TABLE memory_trait_offers DROP CONSTRAINT memory_trait_offers_kind_check;
    ALTER TABLE memory_trait_offers ADD CONSTRAINT memory_trait_offers_kind_check
        CHECK (kind IN ('species', 'class', 'language'));

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    """ + _rebuild("dmbot_web_trait_offers", _LISTING_SWAPS)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
