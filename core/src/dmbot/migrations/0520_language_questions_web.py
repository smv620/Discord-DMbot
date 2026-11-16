"""0520_language_questions_web

The website lists and answers language questions (#4271 part 2, the web half of #4229 slice 1c):
"Your lore says Brewner speaks Giant. Add it?" beside the lineage and class questions.
"""

from __future__ import annotations

# 0451 kept the listing door (`dmbot_web_trait_offers`, 0410) to lineage and class questions until
# the page could word a language one. This lifts that, and teaches the door and the answer door
# (`dmbot_web_trait_answer_add`, 0411) the one rule a language question has that a lineage or class
# one does not: it is no longer open once the character's languages hold the word (the DM's own, or
# the DM's no: `given_by` 'dm' or 'not') or the character has a sheet, since a sheet keeps its own
# languages (#4229). That is `memory/trait_offers._STILL_OPEN`'s clause word for word, so the
# website lists, counts and answers the very questions the bot does. The answers table takes the
# new kind. The bot applies a Yes with `TraitOfferStore.keep` and a No with `never`, as for the
# others (`trait_answer.py`). No row changes, so no row-level security opens. Each door is read back
# from the database as it is and each anchor must be found exactly once, so a drift stops the
# migration, as 0451 does.
_OPEN = (
    "AND NOT (o.kind = 'language' AND (EXISTS (SELECT 1 FROM memory_story_languages s "
    "WHERE s.guild_id = o.guild_id AND s.campaign_id = o.campaign_id "
    "AND s.entity_id = o.entity_id AND s.name_key = o.name_key "
    "AND s.given_by IN ('dm', 'not')) "
    "OR EXISTS (SELECT 1 FROM character_sheets h WHERE h.guild_id = o.guild_id "
    "AND h.campaign_id = o.campaign_id AND h.entity_id = o.entity_id)))"
)
_LISTING_SWAPS = (
    # 0451's filter goes, and the language clause takes its place
    (
        "                  AND o.kind IN ('species', 'class')\n",
        "                  " + _OPEN + "\n",
        1,
    ),
)
_ANSWER_SWAPS = (
    ("k NOT IN ('species', 'class') THEN", "k NOT IN ('species', 'class', 'language') THEN", 1),
    (
        "                         AND e.status = 'confirmed'\n"
        "                         AND NOT EXISTS (SELECT 1 FROM memory_rule_links l\n",
        "                         AND e.status = 'confirmed'\n"
        "                         " + _OPEN + "\n"
        "                         AND NOT EXISTS (SELECT 1 FROM memory_rule_links l\n",
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


SQL = (
    r"""
    ALTER TABLE trait_answers DROP CONSTRAINT trait_answers_kind_check;
    ALTER TABLE trait_answers ADD CONSTRAINT trait_answers_kind_check
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
    """
    + _rebuild("dmbot_web_trait_offers", _LISTING_SWAPS)
    + _rebuild("dmbot_web_trait_answer_add", _ANSWER_SWAPS)
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
