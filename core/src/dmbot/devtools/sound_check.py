"""One call to ElevenLabs' sound generation, to settle the move from `eleven_text_to_sound_v2` to
v3 before v2 stops (#3996, #3310): does the model take `loop: true` and `duration_seconds`, does
it answer an mp3, and what does one sound cost in credits. dev1 runs it once with the real key:

    python -m dmbot.devtools.sound_check --yes                 # v3, loop on, 5 seconds
    python -m dmbot.devtools.sound_check --model eleven_text_to_sound_v2 --yes   # for comparison

It spends a small number of credits and prints counts and words of its own only: the status, the
size and kind of the answer, and the credits used (the account's own count before and after, which
is what the bill uses). The key is read from `ELEVENLABS_API_KEY` and never printed; the sound
description is a fixed line, never anything of a campaign.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from dmbot.recaps.audio.eleven import CREDITS_PER_SOUND, SOUND_MODEL, HttpTransport

PROMPT = "a quiet tavern: low voices, a crackling fire, a cup set down"  # a fixed line
DEFAULT_MODEL = "eleven_text_to_sound_v3"


def kind_of(raw: bytes) -> str:
    """What the answer looks like, from its first bytes: an mp3 starts with an ID3 tag or a frame
    sync; anything else is said as such (never printed)."""
    if raw[:3] == b"ID3" or (len(raw) > 1 and raw[0] == 0xFF and raw[1] & 0xE0 == 0xE0):
        return "mp3"
    return "not an mp3"


def used(raw: bytes) -> int | None:
    """The account's credits used so far, from the subscription answer; None if it can't be read."""
    try:
        value = json.loads(raw)["character_count"]
    except (ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, int) else None


async def check(api_key: str, model: str, seconds: float, loop: bool) -> list[str]:
    transport = HttpTransport(api_key)
    try:
        status, raw = await transport("GET", "/v1/user/subscription", None)
        before = used(raw) if status == 200 else None
        body = {"text": PROMPT, "duration_seconds": seconds, "loop": loop, "model_id": model}
        status, sound = await transport(
            "POST", "/v1/sound-generation?output_format=mp3_44100_128", body
        )
        status2, raw2 = await transport("GET", "/v1/user/subscription", None)
        after = used(raw2) if status2 == 200 else None
    finally:
        await transport.close()
    lines = [f"model {model}, loop {'on' if loop else 'off'}, {seconds:g} s: HTTP {status}"]
    if status == 200:
        lines.append(f"answer: {len(sound)} bytes, {kind_of(sound)}")
    else:
        lines.append("refused: the company's reason is not kept (it could echo the text)")
    if before is not None and after is not None:
        lines.append(
            f"credits used by this call: {after - before} (the code assumes {CREDITS_PER_SOUND})"
        )
    else:
        lines.append("credits: the account's count could not be read; read it in ElevenLabs")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    parser.add_argument("--seconds", type=float, default=5.0, help="the sound's length (0.5 to 30)")
    parser.add_argument("--no-loop", action="store_true", help="ask for no loop")
    parser.add_argument("--yes", action="store_true", help="spend the credits")
    args = parser.parse_args()
    if not 0.5 <= args.seconds <= 30:
        parser.error("--seconds is 0.5 to 30")
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        print("sound_check: ELEVENLABS_API_KEY is not set", file=sys.stderr)
        return 2
    print(
        f"One sound-generation call (the code uses {SOUND_MODEL}); "
        f"about {CREDITS_PER_SOUND} credits."
    )
    if not args.yes:
        print("Nothing was run. Add --yes to spend the credits.")
        return 0
    print("\n".join(asyncio.run(check(key, args.model, args.seconds, not args.no_loop))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
