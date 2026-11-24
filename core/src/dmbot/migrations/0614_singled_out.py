"""0614_singled_out

Session names slice 6 (#5173; docs/design/session_names.md, Supervisor's ruling with the Expert's
check): a thing or a place the story singles out and keeps coming back to, with no name of its own
("the gemstone mine"), is suggested under the story's own words.

- `name_found.how` also takes `singled_out`, so the card can say what made it a suggestion.
- `memory_entities.described`: a lasting mark that a kept entry is a description, not a name: NULL
  for a name, otherwise the words exactly as the story said them, without an article ("gemstone
  mine", "Easthaven ferry", "Garret's old sled"), so everything that writes it into a sentence
  reads "the gemstone mine", and the DM's card says so. The DM giving it a name, or a merge into an
  entry without the mark, sets it back to NULL. At most an entry name's length (`NAME_MAX`).
"""

from __future__ import annotations

SQL = r"""
    -- (the column's inline CHECK of 0608 has Postgres's own name)
    ALTER TABLE name_found DROP CONSTRAINT name_found_how_check;
    ALTER TABLE name_found ADD CONSTRAINT name_found_how_check
        CHECK (how IN ('introduced', 'typed', 'addressed', 'place', 'spoken_of', 'scan',
                       'singled_out'));
    -- Added NULL for every row, not updated, so no row-level security needs opening.
    ALTER TABLE memory_entities ADD COLUMN described TEXT
        CHECK (described IS NULL OR length(described) BETWEEN 1 AND 100);
    """

ISOLATED_TABLES: tuple[str, ...] = ()
