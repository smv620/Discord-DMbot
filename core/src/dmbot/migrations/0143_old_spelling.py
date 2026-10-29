"""0143_old_spelling

A fixed spelling keeps the old one (#2170).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_NAMES,
)

# WEB_NAMES from its first function on (its comments describe the first migration), reused by
# OLD_SPELLING to replace the two functions.
_WEB_NAMES_FUNCTIONS = "    CREATE OR REPLACE FUNCTION dmbot_web_names" + WEB_NAMES.split(
    "    CREATE FUNCTION dmbot_web_names", 1
)[1].replace(
    "    CREATE FUNCTION dmbot_web_set_said_as",
    "    CREATE OR REPLACE FUNCTION dmbot_web_set_said_as",
)

OLD_SPELLING = """
    -- A fixed spelling keeps the old one as an "old spelling" other name (#2170): the cleaner
    -- still matches it, so a line that says it is fixed to the new spelling and a search finds
    -- the entry, but speech-to-text is never told to listen for it.
    ALTER TABLE memory_aliases DROP CONSTRAINT memory_aliases_kind_check;
    ALTER TABLE memory_aliases ADD CONSTRAINT memory_aliases_kind_check
        CHECK (kind IN ('full', 'short', 'nickname', 'title', 'misheard', 'old'));

    -- The website's "said as" list and write door leave old spellings out too: nothing is spoken
    -- from them, so a spelling set on one would be dead data. The same two functions as
    -- WEB_NAMES with that one condition changed; replacing a function resets its search_path, so
    -- it is set again. The grants stay.
    """ + _WEB_NAMES_FUNCTIONS.replace("a.kind <> 'misheard'", "a.kind NOT IN ('misheard', 'old')")

SQL = OLD_SPELLING
