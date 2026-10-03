"""Transcription engine interface.

Phase 1 plugs a real engine (cloud speech-to-text or local Whisper) in behind this
interface. Phase 0 uses the placeholder so the capture pipeline can be verified end to
end before an engine is chosen.
"""

from __future__ import annotations

from typing import Protocol

from dmbot.audio.segmenter import Utterance


class Transcriber(Protocol):
    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        """Return the text spoken in the utterance, or None if nothing usable.

        `hints` are names (characters, NPCs, places) to bias recognition toward.
        """
        ...


class PlaceholderTranscriber:
    """Returns no text. Lets the pipeline run before a real engine is configured."""

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        return None
