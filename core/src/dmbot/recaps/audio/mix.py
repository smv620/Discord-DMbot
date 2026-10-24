"""The mix plan (#1413): the ffmpeg command that joins the voice, the music and the ambience.

Only strings are built here; nothing is run. Levels (what the owner asked for, #1383): the voice
at -16 LUFS with a -1 dBTP ceiling, the music at -32 LUFS and pushed down about 6 dB while the
voice speaks (a side-chain compressor keyed by the voice), the ambience at -38 LUFS. All three are
cut or padded to the voice's length, so the stems line up when played together. Outputs: the
voice stem, the bed stem (music plus ambience) and the mix of both.

**The ending (owner, 2026-10-24, #2629):** the music is never cut off at full volume. It runs
1.5 seconds past the last spoken word (the render pads the voice track by that much), and its end
is faded as a safety on top of the composed ending (`music.py` asks the composer for one): a
diminuendo ending fades over the last 1.5 s; a crescendo ending, which should leave a held chord
ringing, gets only a short 0.3 s fade so a chord past the cut never clicks off. The ambience bed
fades out the same way, with the music.

Needs ffmpeg 4.4 or newer (`amix normalize=0`). Loudness here is a single pass of `loudnorm`,
which is close, not exact; the 6 dB of ducking depends on how loud the speech is, so the numbers
are a starting point for the by-ear test on the server (dev1), kept as constants for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

VOICE_LUFS = -16
VOICE_PEAK_DB = -1  # true-peak ceiling
# owner, 2026-10-20: music at 30% of the earlier level (-22 and -28 LUFS), a touch of ambience
# rather than something distracting. The ambience stays 6 dB under the music, as before.
MUSIC_LUFS = -32
AMBIENCE_LUFS = -38
BED_PEAK_DB = -2
DUCK_DB = 6  # how far the music goes down under speech, about
DUCK_RATIO = 4
# A threshold DUCK_DB / (1 - 1/ratio) = 8 dB under the speech's usual level makes the
# compressor take about DUCK_DB off; speech sits near -20 dBFS after levelling.
DUCK_THRESHOLD_DB = -28
DUCK_ATTACK_MS = 30
DUCK_RELEASE_MS = 500
LRA = 11


RATE = 44100  # loudnorm hands back 192 kHz: every branch is brought down to this
DIMINUENDO_FADE_S = 1.5  # the safety fade of a diminuendo ending: the whole tail after the voice
CRESCENDO_FADE_S = 0.3  # and of a crescendo ending: just enough that a ringing chord never clicks


def _norm(lufs: int, peak: int) -> str:
    return f"loudnorm=I={lufs}:TP={peak}:LRA={LRA},aresample={RATE}"


def _fit(seconds: float) -> str:
    """Cut at the voice's length, and pad with silence if shorter."""
    return f"atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={seconds:.3f}"


def _fade_out(seconds: float, ending: str) -> str:
    """The music's (and the ambience's) closing fade for this kind of ending."""
    length = min(CRESCENDO_FADE_S if ending == "crescendo" else DIMINUENDO_FADE_S, seconds)
    return f"afade=t=out:st={max(0.0, seconds - length):.3f}:d={length:.3f}"


@dataclass(frozen=True, slots=True)
class MixPlan:
    filter_complex: str
    with_music: bool = True
    with_ambience: bool = True
    voice_out: str = "[voice_out]"
    bed_out: str = "[bed_out]"
    mix_out: str = "[mix_out]"


def plan(
    voice_seconds: float,
    *,
    with_music: bool = True,
    with_ambience: bool = True,
    ending: str = "diminuendo",
) -> MixPlan:
    """The filter graph. `ending`: "diminuendo" or "crescendo" (anything else fades down), how
    the music and the ambience close (#2629). Inputs are numbered in the order `command` adds
    them: the voice is 0, then the music (if any), then the ambience (if any). A missing track
    leaves its branch out;
    the bed is then what is there (or silence if neither is)."""
    seconds = max(0.001, float(voice_seconds))
    music_at = 1 if with_music else None
    ambience_at = (2 if with_music else 1) if with_ambience else None
    keys = "[voice_main][voice_key][voice_stem]" if with_music else "[voice_main][voice_stem]"
    graph = [
        f"[0:a]{_norm(VOICE_LUFS, VOICE_PEAK_DB)},{_fit(seconds)},"
        f"asplit={3 if with_music else 2}{keys}"
    ]
    bed: list[str] = []
    if music_at is not None:
        graph.append(
            f"[{music_at}:a]{_norm(MUSIC_LUFS, BED_PEAK_DB)},{_fit(seconds)},"
            f"{_fade_out(seconds, ending)}[music_raw]"
        )
        graph.append(
            "[music_raw][voice_key]sidechaincompress="
            f"threshold={_db_to_linear(DUCK_THRESHOLD_DB):.4f}:ratio={DUCK_RATIO}"
            f":attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[music_ducked]"
        )
        bed.append("[music_ducked]")
    if ambience_at is not None:
        graph.append(
            f"[{ambience_at}:a]{_norm(AMBIENCE_LUFS, BED_PEAK_DB)},{_fit(seconds)},"
            f"{_fade_out(seconds, ending)}[ambience_fit]"
        )
        bed.append("[ambience_fit]")
    if len(bed) == 2:
        graph.append(f"{''.join(bed)}amix=inputs=2:duration=first:normalize=0[bed_mixed]")
    elif bed:
        graph.append(f"{bed[0]}anull[bed_mixed]")
    else:
        graph.append(f"anullsrc=r=44100:cl=stereo,{_fit(seconds)}[bed_mixed]")
    graph.append("[bed_mixed]asplit=2[bed_mix][bed_out]")
    # Voice plus bed is louder than the voice alone: the mix is levelled again to the voice's
    # target, which also holds its true peak at the ceiling.
    graph.append(
        "[voice_main][bed_mix]amix=inputs=2:duration=first:normalize=0,"
        f"{_norm(VOICE_LUFS, VOICE_PEAK_DB)}[mix_out]"
    )
    graph.append("[voice_stem]anull[voice_out]")
    return MixPlan(";".join(graph), with_music, with_ambience)


def _db_to_linear(db: float) -> float:
    return float(10 ** (db / 20))


def command(
    mix: MixPlan,
    *,
    voice: str,
    music: str | None,
    ambience: str | None,
    out_voice: str,
    out_bed: str,
    out_mix: str,
) -> list[str]:
    """The ffmpeg argument list (not run here). The ambience loops without end; the graph cuts
    it to the voice's length."""
    if (music is not None) != mix.with_music or (ambience is not None) != mix.with_ambience:
        raise ValueError("the plan was made for different tracks than these")
    args = ["ffmpeg", "-y", "-i", voice]
    if music:
        args += ["-i", music]
    if ambience:
        args += ["-stream_loop", "-1", "-i", ambience]
    args += ["-filter_complex", mix.filter_complex]
    for label, path in ((mix.voice_out, out_voice), (mix.bed_out, out_bed), (mix.mix_out, out_mix)):
        args += ["-map", label, path]
    return args
