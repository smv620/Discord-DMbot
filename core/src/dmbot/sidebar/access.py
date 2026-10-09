"""Who may read the DM sidebar lines in a raw transcript (#933, #935). Pure.

The owner has not answered #933 yet, so the build is option 1 of that question: the
campaign's DM only, in their own as-heard download. The lines are never in the cleaned
transcript and never in the live transcript channel, whatever this says.
"""

from __future__ import annotations

from collections.abc import Collection

# The one line to change when #933 is answered: "dm" (option 1), "everyone" (option 2).
# Option 3 ("follow the DM-screen setting") also needs the viewer's peek state.
SIDEBAR_READERS = "dm"


def may_read(campaign_dms: Collection[int], viewer_id: int | None) -> bool:
    """Whether this person's as-heard download includes the sidebar lines."""
    if SIDEBAR_READERS == "everyone":
        return True
    return viewer_id is not None and viewer_id in campaign_dms
