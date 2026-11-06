"""0191_backstory_link_words

Catch-all words on waiting links (#2454).
"""

from __future__ import annotations

BACKSTORY_LINK_WORDS = """
    -- A stated relationship no fixed kind fits is kept on the catch-all with the words the text
    -- used (#2454), so a waiting connection has to carry them until the DM keeps it.
    ALTER TABLE backstory_links ADD COLUMN detail TEXT NOT NULL DEFAULT '';
"""

SQL = BACKSTORY_LINK_WORDS
