"""0206_timeline_stable

The timeline door only reads (#2485).
"""

from __future__ import annotations

TIMELINE_STABLE = """
    -- The story timeline's door only reads (#2462), so it is STABLE: the planner may reuse its
    -- answer inside one statement. 0196 created it VOLATILE, the default; changing the volatility
    -- keeps its pinned search_path, its owner and its grants. No rows change.
    ALTER FUNCTION dmbot_web_story_timeline(BIGINT, TEXT) STABLE;
"""

SQL = TIMELINE_STABLE
