"""0095_web_transcript_pages

Transcript pages and characters (#1512).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_TRANSCRIPT_PAGES,
)

SQL = WEB_TRANSCRIPT_PAGES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # Who plays which character in a campaign, for the transcript's labels (#1512).
    ("dmbot_web_transcript_characters", "dmbot_web_transcript_characters(BIGINT, TEXT)"),
)
