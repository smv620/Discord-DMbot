"""The one lock order for deleting a person's words (#4153): every session row they have lines in,
`FOR UPDATE`, in id order, before anything that cites those lines. "Stop recording me", "Delete my
transcripts" and a DM's removal of a speaker all take it this way, and the after-session writers
hold one session's row (never a second) while they re-read who is recorded, so a writer that is
mid-write finishes first and the deletion then removes what it wrote."""

from __future__ import annotations

from typing import Any


async def lock_sessions_of(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> None:
    """Lock the session rows `user_id` has lines in, in this server (`campaign_id`: only that
    campaign's), in the caller's transaction, until it ends."""
    await conn.execute(
        "SELECT 1 FROM transcript_sessions WHERE guild_id = %s"
        " AND (%s::text IS NULL OR campaign_id = %s)"
        " AND id IN (SELECT session_id FROM transcript_lines WHERE guild_id = %s AND user_id = %s)"
        " ORDER BY id FOR UPDATE",
        (guild_id, campaign_id, campaign_id, guild_id, user_id),
    )
