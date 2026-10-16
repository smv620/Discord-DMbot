"""The mix plan (#1413): the ffmpeg command that joins the voice, the music and the ambience.

Only strings are built here; nothing is run. Levels (what the owner asked for, #1383): the voice
at -16 LUFS with a -1 dBTP ceiling, the music at -22 LUFS and pushed down about 6 dB while the
voice speaks (a side-chain compressor keyed by the voice), the ambience at -28 LUFS. All three are
cut or padded to the voice's length, so the stems line up when played together. Outputs: the
voice stem, the bed stem (music plus ambience) and the mix of both.

Loudness here is a single pass of `loudnorm`, which is close, not exact; the 6 dB of ducking
depends on how loud the speech is, so the numbers are a starting point for the by-ear test on the
server (dev1), kept as constants for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

VOICE_LUFS = -16
VOICE_PEAK_DB = -1  # true-peak ceiling
MUSIC_LUFS = -22
AMBIENCE_LUFS = -28
BED_PEAK_DB = -2
DUCK_DB = 6  # how far the music goes down under speech, about
DUCK_RATIO = 4
# A threshold DUCK_DB / (1 - 1/ratio) = 8 dB under the speech's usual level makes the
# compressor take about DUCK_DB off; speech sits near -20 dBFS after levelling.
DUCK_THRESHOLD_DB = -28
DUCK_ATTACK_MS = 30
DUCK_RELEASE_MS = 500
LRA = 11


def _norm(lufs: int, peak: int) -> str:
    return f"loudnorm=I={lufs}:TP={peak}:LRA={LRA}"


def _fit(seconds: float) -> str:
    """Cut at the voice's length, and pad with silence if shorter."""
    return f"atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={seconds:.3f}"


@dataclass(frozen=True, slots=True)
class MixPlan:
    filter_complex: str
    voice_out: str = "[voice_out]"
    bed_out: str = "[bed_out]"
    mix_out: str = "[mix_out]"


def plan(voice_seconds: float, *, with_music: bool = True, with_ambience: bool = True) -> MixPlan:
    """The filter graph. Inputs are numbered in the order `command` adds them: the voice is 0,
    then the music (if any), then the ambience (if any). A missing track leaves its branch out;
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
        graph.append(f"[{music_at}:a]{_norm(MUSIC_LUFS, BED_PEAK_DB)},{_fit(seconds)}[music_raw]")
        graph.append(
            "[music_raw][voice_key]sidechaincompress="
            f"threshold={_db_to_linear(DUCK_THRESHOLD_DB):.4f}:ratio={DUCK_RATIO}"
            f":attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[music_ducked]"
        )
        bed.append("[music_ducked]")
    if ambience_at is not None:
        graph.append(
            f"[{ambience_at}:a]{_norm(AMBIENCE_LUFS, BED_PEAK_DB)},{_fit(seconds)}[ambience_fit]"
        )
        bed.append("[ambience_fit]")
    if len(bed) == 2:
        graph.append(f"{''.join(bed)}amix=inputs=2:duration=first:normalize=0[bed_mixed]")
    elif bed:
        graph.append(f"{bed[0]}anull[bed_mixed]")
    else:
        graph.append(f"anullsrc=r=44100:cl=stereo,{_fit(seconds)}[bed_mixed]")
    graph.append("[bed_mixed]asplit=2[bed_mix][bed_out]")
    # The sum of voice and bed can pass the voice's own ceiling: a limiter holds it at -1 dB.
    graph.append(
        "[voice_main][bed_mix]amix=inputs=2:duration=first:normalize=0,"
        f"alimiter=limit={_db_to_linear(VOICE_PEAK_DB):.3f}[mix_out]"
    )
    graph.append("[voice_stem]anull[voice_out]")
    return MixPlan(";".join(graph))


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
    args = ["ffmpeg", "-y", "-i", voice]
    if music:
        args += ["-i", music]
    if ambience:
        args += ["-stream_loop", "-1", "-i", ambience]
    args += ["-filter_complex", mix.filter_complex]
    for label, path in ((mix.voice_out, out_voice), (mix.bed_out, out_bed), (mix.mix_out, out_mix)):
        args += ["-map", label, path]
    return args
