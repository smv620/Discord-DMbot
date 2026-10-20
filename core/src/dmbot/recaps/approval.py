"""Approving a recap's text before any audio (#2083): the fingerprint an approval is tied to, and
the four things the DM may pick.

Approval is recorded with the hash of the title and summary as they were approved
(`recap_jobs.approved_hash`). It counts only while that hash matches the text, so an edit after
approval ends it without anything having to clear it. No audio is queued, and no ElevenLabs call
is made, for a recap whose approval does not match its text.
"""

from __future__ import annotations

import hashlib

VOICE = "voice"
MUSIC = "music"
BOTH = "both"
TEXT = "text"  # approved, no audio
CHOICES = (VOICE, MUSIC, BOTH, TEXT)
LABELS = {
    VOICE: "🎙️ Voice-over",
    MUSIC: "🎵 Background music",
    BOTH: "Both",
    TEXT: "Text only",
}


def text_hash(title: str, summary: str) -> str:
    """A fingerprint of the text the audio is made from and the DM approves. The database's door
    computes the same value in SQL (`sha256(title || E'\\n' || summary)`), so the two agree."""
    return hashlib.sha256(f"{title}\n{summary}".encode()).hexdigest()


def choices_for(*, audio_in_plan: bool) -> tuple[str, ...]:
    """What the DM may pick: only text where the plan has no audio (or the key or switch is off)."""
    return CHOICES if audio_in_plan else (TEXT,)
