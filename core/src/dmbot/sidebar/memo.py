"""Reading a Discord voice message (#935). Pure apart from PyAV decoding bytes in memory.

A voice message is a short Ogg Opus file. It becomes the same 16 kHz mono 16-bit audio the
table's speech is, so it goes through the same speech-to-text. Nothing is written to disk:
the audio is dropped as soon as it is transcribed.
"""

from __future__ import annotations

from dmbot import audio_decode

MAX_MEMO_S = 30.0  # a question is short; a long memo would hold the speech worker
MAX_MEMO_BYTES = 1_500_000  # about 30 s of Discord's Opus with room to spare
MIN_MEMO_S = 0.4
TOO_LONG = f"Too long. Keep it under {int(MAX_MEMO_S)} seconds."


class MemoError(RuntimeError):
    """The file can't be read as a voice message. The message is plain words for the DM."""


def seconds(pcm: bytes) -> float:
    return audio_decode.seconds(pcm)


# What each refusal says to the DM; the reading itself is shared (#2162).
_WORDS = {
    audio_decode.Why.TOO_BIG: TOO_LONG,
    audio_decode.Why.TOO_LONG: TOO_LONG,
    audio_decode.Why.NO_SOUND: "That file has no sound. Send a voice message instead.",
    audio_decode.Why.UNREADABLE: "I couldn't read that voice message. Try sending it again.",
    audio_decode.Why.TOO_SLOW: "I couldn't read that voice message. Try sending it again.",
    audio_decode.Why.TOO_SHORT: "Too short to hear. Try again.",
}


def decode(data: bytes) -> bytes:
    """The voice message as 16 kHz mono s16le PCM. Blocking: run it off the event loop."""
    try:
        return audio_decode.decode_pcm(
            data,
            container="ogg",  # what Discord sends
            max_bytes=MAX_MEMO_BYTES,
            max_s=MAX_MEMO_S,
            min_s=MIN_MEMO_S,
        )
    except audio_decode.Refused as refused:
        raise MemoError(_WORDS[refused.why]) from refused
