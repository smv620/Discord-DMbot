"""0620_recap_lines_noted

The note about lines read as last time's recap is said once for a session (#5232).
"""

from __future__ import annotations

# The story reader says "N lines read as last time's recap" in the DM Bot channel each time it
# finishes a read of an imported session, and it reads one session several times (a retry, a resumed
# read, lines put back), so the DM saw the same note again and again. The session now remembers
# that it was said: the time, null until then. It is claimed by one update before the post (so two
# reads at once say it once) and let go again if the post fails (so it is tried again). A column
# with no default and no backfill: nothing already posted is cleaned up, and a session read before
# this says its note at most once more. Not a word of the session is kept: a time only.
SQL = r"""
    ALTER TABLE transcript_sessions ADD COLUMN recap_lines_noted_at BIGINT;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
