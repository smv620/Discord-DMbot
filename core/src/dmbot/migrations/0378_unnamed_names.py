"""0378_unnamed_names

Every person the story never named is called "unnamed - <description>" (#3736): the stored copies
of the old "<description> (name unknown)" labels are rewritten, and two that would read the same
are told apart by when they were first seen.
"""

from __future__ import annotations

# The name of an unnamed person is built every time from its parts (`memory/unnamed.label`), but a
# copy is kept in `memory_entities.name`, and a person the DM joined into a named entry leaves that
# copy behind as one of its other names (`memory_aliases`, with its lookup `key`). Both are
# rewritten the way `unnamed.label` now builds them: the old label was "<body> (name unknown)", a
# chain "<anchor body> (name unknown)'s <word> (name unknown)", and the new one is "unnamed -
# <body>" with each inner suffix gone ("unnamed - Ken's sister's son"). Only a label a join really
# left (its unnamed person merged into that entry) is rewritten among the other names. The new
# form is never
# longer than the old (10 characters in, at least 15 out), so the 100-character limit holds.
#
# Two live people whose names now read the same (two hooded figures) are told apart the way a new
# one is (`unnamed.unique_what`): the later one, by when it was made, gets "(first seen 7 Nov
# 2026)" (UTC), then that replaced by the date and time if that is not enough. Its description
# gets the same words, so the name built from the parts agrees with the stored copy. A rewritten
# other name's sound codes are cleared rather than worked out here (they need Python): a label is
# never something said aloud, and an empty list only means no word is matched to it by sound.
#
# Migrations run unscoped and both tables have forced row-level security, so each statement opens
# a policy for itself first, on every table it reads (CLAUDE.md, the #704 and #781 lessons).
# The old label without its endings: every whole " (name unknown)", then one an anchor cut to 40
# characters left half there just before its "'s" (only a start of "(name unknown)" matches, so a
# description's own words never do). `unnamed.from_old_label` is the same in Python.
_PART = "n(?:a(?:m(?:e(?: (?:u(?:n(?:k(?:n(?:o(?:w(?:n)?)?)?)?)?)?)?)?)?)?)?"
_TEXT = (
    "regexp_replace(regexp_replace({col}, ' \\(name unknown\\)', '', 'g'), "
    "' \\(" + _PART + "(?=''s )', '', 'g')"
)
# The same on a key (`name_key` of the text: apostrophes dropped and brackets made spaces, so the
# inner ending of a chain reads "name unknown s son", and a cut one "name unk s son").
_KEY = (
    "regexp_replace(regexp_replace(regexp_replace({col}, ' name unknown s( |$)', 's\\1', 'g'), "
    "' " + _PART + " s( |$)', 's\\1', 'g'), ' name unknown$', '', 'g')"
)
_FIRST_SEEN = r"""
    WITH ranked AS (
        SELECT e.guild_id, e.campaign_id, e.id,
               ' (first seen ' || to_char(to_timestamp(e.created_at) AT TIME ZONE 'UTC', {fmt})
               || ')' AS tag,
               row_number() OVER (PARTITION BY e.guild_id, e.campaign_id,
                                               regexp_replace(lower(e.name), '[^[:alnum:] ]', '',
                                                              'g')
                                  ORDER BY e.created_at, e.id) AS n
        FROM memory_entities e
        WHERE e.unnamed AND e.status IN ('proposed', 'confirmed') AND e.merged_into IS NULL
    )
    UPDATE memory_entities e
    SET unnamed_what = left(regexp_replace(e.unnamed_what, ' \(first seen [^)]*\)$', ''),
                            100 - length(r.tag)) || r.tag,
        name = left(regexp_replace(e.name, ' \(first seen [^)]*\)$', ''),
                    100 - length(r.tag)) || r.tag
    FROM ranked r
    WHERE r.n > 1 AND e.guild_id = r.guild_id AND e.campaign_id = r.campaign_id AND e.id = r.id;
"""

SQL = (
    r"""
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_aliases FOR ALL USING (true) WITH CHECK (true);

    -- the other names a joined person left behind (before the entries, while the old names still
    -- match them)
    UPDATE memory_aliases a
    SET text = 'unnamed - ' || """
    + _TEXT.format(col="a.text")
    + r""",
        key = 'unnamed ' || """
    + _KEY.format(col="a.key")
    + r""",
        sound_codes = '{}'
    WHERE a.text LIKE '% (name unknown)'
      AND EXISTS (SELECT 1 FROM memory_entities m
                  WHERE m.guild_id = a.guild_id AND m.campaign_id = a.campaign_id
                    AND m.unnamed AND m.merged_into = a.entity_id AND m.name = a.text);

    UPDATE memory_entities
    SET name = 'unnamed - ' || """
    + _TEXT.format(col="name")
    + r"""
    WHERE unnamed AND name LIKE '% (name unknown)';
"""
    + _FIRST_SEEN.format(fmt="'FMDD Mon YYYY'")
    + _FIRST_SEEN.format(fmt="'FMDD Mon YYYY HH24:MI'")
    + r"""
    DROP POLICY migrate_backfill ON memory_aliases;
    DROP POLICY migrate_backfill ON memory_entities;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
