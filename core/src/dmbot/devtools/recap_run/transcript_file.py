"""A transcript file read for a recap test run (#1969; the parser itself lives in
`dmbot.transcript.importer` since #2105). Pure: no database, no network, no AI.

The owner can test a recap on a game that was played before DMbot was listening, or recorded with
another tool. This module turns such a file into the same `Line`s the stored sessions give the
recap worker, and reads the optional names file (`--names`). Four shapes are accepted and told
apart by their content, never by the file's name; anything else is refused with a line that names
them (`FORMATS`):

* DMbot's own download from the website (raw or cleaned): `[0:42:10] (Mia) {Cerric}: words`;
* plain lines, `Name: words`, with an optional `[hh:mm:ss]` first. Craig's plain-text export is a
  known source (#1977), so this reader is forgiving: up to 10 header lines before the first
  utterance are skipped, a line that is not `Name: words` after an utterance is that utterance
  carrying on (joined with a space; refused if over a quarter of the lines would be joined, as
  then it is not this format), and a name may hold `( ) # . ' _` (`Rowan (rowan_v)`, `kai#0`).
  Craig marks talk it cut out with a braced placeholder, `{non-game context}` (#1995; the only one
  seen so far): a line that is only such a placeholder is dropped and counted apart, never as a
  joined line, and a placeholder at the start of an utterance is stripped (an utterance left empty
  is dropped);
* WebVTT and SRT whose cue text starts `Name:`.

Who is who: every speaker gets a made-up number in the order they first speak, and the name given
with `--dm` is the DM's. Nothing here prints a line of the file; errors name the format or the
speakers, never the words. The file is not a customer's data and is never stored: the caller
keeps it in memory for the run.
"""

from __future__ import annotations

from dmbot.recaps.audio.names import SpokenName
from dmbot.recaps.audio.script import EMOTION, PERFORMANCE_KIND, Script
from dmbot.recaps.text import CUE, PERFORMANCE
from dmbot.transcript.importer import (
    DMBOT,
    FORMATS,
    LINE_MS,
    PLAIN,
    SPEAKER_BASE,
    SRT,
    VTT,
    Transcript,
    TranscriptError,
    Utterance,
    detect,
    parse,
)
from dmbot.transcript.models import Line

# What the tool and its tests read from here, although the parser lives in the importer now.
__all__ = [
    "CONSENT_QUESTION",
    "DMBOT",
    "FORMATS",
    "LINE_MS",
    "PLAIN",
    "SPEAKER_BASE",
    "SRT",
    "VTT",
    "Transcript",
    "TranscriptError",
    "Utterance",
    "consent_given",
    "cue_map",
    "detect",
    "dm_id",
    "lines_of",
    "parse",
    "parse_names",
    "speaker_ids",
]


def speaker_ids(transcript: Transcript) -> dict[str, int]:
    """A made-up number for each speaker, in the order they first speak."""
    return {name: SPEAKER_BASE + n for n, name in enumerate(transcript.speakers)}


def dm_id(transcript: Transcript, dm: str) -> int:
    """The number of the speaker named `dm` (case ignored). Raises if the file has no such
    speaker, naming who it does have so the name can be fixed."""
    ids = speaker_ids(transcript)
    wanted = dm.strip().casefold()
    for name, number in ids.items():
        if name.casefold() == wanted:
            return number
    # "Rowan (rowan_v)": the part before the parenthesis is the name people use (#1977).
    short = [n for name, n in ids.items() if _before_parenthesis(name) == wanted]
    if len(short) == 1:
        return short[0]
    if len(short) > 1:
        raise TranscriptError(f"--dm {dm!r} fits more than one speaker; give the whole name")
    raise TranscriptError(
        f"--dm {dm!r} isn't a speaker in the file. The speakers are: " + ", ".join(sorted(ids))
    )


def _before_parenthesis(name: str) -> str:
    """ "Rowan (rowan_v)" is "rowan" (case ignored); a name not ending in ")" has no short form."""
    stripped = name.strip()
    if stripped.endswith(")") and " (" in stripped:
        return stripped.rsplit(" (", 1)[0].strip().casefold()
    return ""


def lines_of(transcript: Transcript) -> list[Line]:
    """The file as the recap worker reads a session: one line per utterance, in the file's order
    (a file is written in the order things were said; sorting by time would scramble one whose
    times restart or are missing)."""
    ids = speaker_ids(transcript)
    return [Line(u.at_ms, ids[u.speaker], u.text, u.text) for u in transcript.utterances]


def parse_names(text: str) -> list[SpokenName]:
    """The names file: one name a line, `Ka'zeth = Kah-zeth` to give how it is said, and an
    optional `secret:` first for a name the recap must never use. Blank lines and `#` lines are
    ignored."""
    out: list[SpokenName] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        secret = False
        if line.casefold().startswith("secret:"):
            secret = True
            line = line[len("secret:") :].strip()
        name, _, said = line.partition("=")
        name, said = name.strip(), said.strip()
        if not name:
            raise TranscriptError(f"line {number} of the names file has no name")
        out.append(SpokenName(name, said or None, secret=secret))
    return out


def cue_map(summary: str, script: Script) -> list[str]:
    """Every `《emotion》` and `((performance))` cue in the narration, in order, with the voice
    tag it became or "music only" (a cue the voice has no matching tag for still moves the
    music)."""
    found: list[tuple[int, str, str]] = []
    for match in CUE.finditer(summary):
        found.append((match.start(), EMOTION, match.group()[1:-1]))
    for match in PERFORMANCE.finditer(summary):
        found.append((match.start(), PERFORMANCE_KIND, match.group()[2:-2]))
    found.sort()
    tags: list[tuple[str, str, str]] = [(c.kind, c.name, c.tag) for c in script.cues]
    rows: list[str] = []
    for _, kind, name in found:
        tag = ""
        for i, (k, n, t) in enumerate(tags):
            if k == kind and n == name.strip().lower():
                tag = t
                del tags[i]
                break
        arrow = tag or "music only"
        shown = f"《{name}》" if kind == EMOTION else f"(({name}))"
        rows.append(f"  {shown} -> {arrow}")
    return rows


CONSENT_QUESTION = (
    "Did everyone whose words are in this file agree to be recorded, to this test, and to their "
    "words being sent to an AI service? (yes/no) "
)


def consent_given(answer: str) -> bool:
    """Only the word yes. `--yes` covers the cost question and never this one."""
    return answer.strip().lower() == "yes"
