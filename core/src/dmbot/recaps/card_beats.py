"""Hands a session's recap beats to the character card's job (#5050). It lives here because the
memory package may not read recaps (the layers contract), so the bot passes this reader in."""

from __future__ import annotations

from typing import Any

from dmbot.recaps import beats as recap_beats


class RecapBeats:
    """`card_mentions.Recaps` over the recap store: the beats a session's recap kept, or none (no
    recap yet, or one with no beat sheet)."""

    def __init__(self, recaps: Any) -> None:
        self._recaps = recaps

    async def beats(
        self, guild_id: int, campaign_id: str, session_at: int
    ) -> list[recap_beats.Beat]:
        job = await self._recaps.get(guild_id, campaign_id, session_at)
        if job is None or not getattr(job, "beats", None):
            return []
        return recap_beats.from_json(job.beats)
