"""0614_singled_out

Session names slice 6 (#5173; docs/design/session_names.md, Supervisor's ruling with the Expert's
check): a thing or a place the story singles out and keeps coming back to, with no name of its own
("the gemstone mine"), is suggested under the story's own words.

- `name_found.how` also takes `singled_out`, so the card can say what made it a suggestion.
- `memory_entities.described`: a lasting mark that a kept entry is a description, not a name, so
  everything that writes it into a sentence reads "the gemstone mine", and the DM's card says so.
  The DM giving it a name, or a merge into an entry without the mark, clears it.
"""

from __future__ import annotations

SQL = r"""
    -- (the column's inline CHECK of 0608 has Postgres's own name)
    ALTER TABLE name_found DROP CONSTRAINT name_found_how_check;
    ALTER TABLE name_found ADD CONSTRAINT name_found_how_check
        CHECK (how IN ('introduced', 'typed', 'addressed', 'place', 'spoken_of', 'scan',
                       'singled_out'));
    -- Added with a default, not updated, so no row-level security needs opening.
    ALTER TABLE memory_entities ADD COLUMN described BOOLEAN NOT NULL DEFAULT false;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
