"""0501_waiting_replaces

What a keep would replace, stored on each waiting connection, and returned by the waiting door as
`now` (#4441, after 0499's same-kind match).
"""

from __future__ import annotations

# 0499 gave the website's connection card a "Now:" from a held connection of the same kind between
# the same two entries. The bot decides with the ontology what a keep really ends (an ally against
# an enemy, a feeling that changed), so the website could say "Add it" while the press ended
# something.
# The bot now writes what a keep would end on the waiting row itself (`replaces`, a JSON list of
# `{predicate, detail, hidden}`, read again by the bot every minute while anything waits;
# `memory/waiting_now.py`), and the door returns that, so Discord's card and the website's can
# never differ.
#
# Two columns, both new with a default (nothing changes for an old row; no row-level security
# opens): `replaces` (the list, empty for a new connection) and `replaces_at` (when what a keep
# would replace last changed, 0 for never). The door: 0499's `'now', (SELECT ... LIMIT 1),` is
# swapped for the stored list, as `{items, hidden}`; `hidden` is true when every held connection is
# hidden from the players or an end of the pair is audience-limited (as 0499 said). The swap is
# found by its first and last lines, each exactly once, so a drift stops the migration (the 0403
# and 0436 way).
_START = "'now', (SELECT jsonb_build_object("
_END = "ORDER BY r.created_at DESC, r.id LIMIT 1),"
_NEW = (
    "'now', CASE WHEN jsonb_array_length(l.replaces) = 0 THEN NULL ELSE jsonb_build_object(\n"
    "                                'items', l.replaces,\n"
    "                                'hidden', NOT EXISTS (SELECT 1 FROM jsonb_array_elements("
    "l.replaces) x\n"
    "                                                      WHERE NOT (x->>'hidden')::BOOLEAN)\n"
    "                                          OR COALESCE((SELECT bool_or(e.audience_limited)\n"
    "                                                FROM memory_entities e\n"
    "                                                WHERE e.guild_id = g AND e.campaign_id = c\n"
    "                                                  AND e.id IN (l.subject_id, l.object_id)),"
    " false)) END,"
)

SQL = (
    r"""
    ALTER TABLE backstory_links
        ADD COLUMN replaces JSONB NOT NULL DEFAULT '[]'::jsonb
            CHECK (jsonb_typeof(replaces) = 'array' AND jsonb_array_length(replaces) <= 20),
        ADD COLUMN replaces_at BIGINT NOT NULL DEFAULT 0;

    DO $do$
    DECLARE
        def text;
        a integer;
        b integer;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_waiting';
        IF (length(def) - length(replace(def, $q$"""
    + _START
    + r"""$q$, ''))) <> length($q$"""
    + _START
    + r"""$q$)
           OR (length(def) - length(replace(def, $q$"""
    + _END
    + r"""$q$, ''))) <> length($q$"""
    + _END
    + r"""$q$) THEN
            RAISE EXCEPTION 'the waiting door did not have the expected shape';
        END IF;
        a := position($q$"""
    + _START
    + r"""$q$ IN def);
        b := position($q$"""
    + _END
    + r"""$q$ IN def);
        IF b < a THEN
            RAISE EXCEPTION 'the waiting door did not have the expected shape';
        END IF;
        def := left(def, a - 1) || $q$"""
    + _NEW
    + r"""$q$ || substr(def, b + length($q$"""
    + _END
    + r"""$q$));
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
