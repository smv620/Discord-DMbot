"""`recap_run --score` (#3328): per must-have, was it covered, missed, or not given by the session,
and how the draft did on the fact check. Counts and line numbers only, so a run can be posted on
GitHub. Every PR that changes the recap writer or its instructions carries this before and after,
run on the same fixed set of sessions (docs/RECAP_STANDARD.md).

The states come from the same check the critics loop uses (`recaps.coverage`), asked once of the
finished recap; nothing is written to any campaign.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dmbot.recaps import beats as bt
from dmbot.recaps import coverage as covg
from dmbot.recaps import draft as rd
from dmbot.recaps import must_haves
from dmbot.recaps.pipeline import Client


def must_list(musts: Sequence[str]) -> list[str]:
    """The list scored: the campaign's own, or DMbot's defaults (a golden or transcript run has no
    campaign list)."""
    return list(musts) or must_haves.default_texts()


BEATS_FILE = "beats.json"  # the beat sheet a `--out` run saves, for `--score-recap` (#3328)


def format_score(
    states: dict[int, str] | None, total: int, checks: Sequence[tuple[str, bool, str]] | None
) -> list[str]:
    """The report lines. `states` None: the check's answer could not be read. `checks` None: the
    recap was scored from a file, so there is no fact check to report."""
    out = ["", f"Must-haves ({total} lines):"]
    if states is None:
        out.append("  the coverage check could not be read this time")
    else:
        tally = covg.counts(states, total)
        out.append(
            f"  covered {tally[covg.COVERED]}, missed {tally[covg.MISSED]}, "
            f"not given {tally[covg.NOT_GIVEN]}, unread {tally['unread']}"
        )
        out += [f"  {n:>2}  {states.get(n, 'unread')}" for n in range(1, total + 1)]
    if checks is not None:
        passed = sum(1 for _, ok, _ in checks if ok)
        out.append(f"Fact check and the final checks: {passed} of {len(checks)} passed")
    return out


async def score(
    summary: str,
    sheet: Sequence[dict[str, Any]] | None,
    musts: Sequence[str],
    checks: Sequence[tuple[str, bool, str]] | None,
    client: Client,
    script: str = "",
) -> list[str]:
    """Ask the coverage check about a finished recap and format the counts. A recap that is not
    ready has nothing to score: the caller does not ask. `script`: the session's script, given to
    the check as the live path gives it (#3901), so the score can be run with and without it."""
    wanted = must_list(musts)
    beats = bt.from_json(sheet) if sheet else []
    sentences = rd.sentences_of(summary)
    try:
        reply = await client.complete(
            covg.system(script),
            covg.message(wanted, beats, sentences, script),
            max_tokens=covg.room(len(wanted)),
            schema=covg.SCHEMA,
        )
    except Exception as exc:  # the type only: never the text
        return ["", f"Must-haves: the coverage check failed ({type(exc).__name__})"]
    return format_score(covg.parse(reply.text, len(wanted)), len(wanted), checks)


def recap_from_file(text: str) -> str:
    """The recap in a saved `recap.txt` (its cue-free section, as the table reads it), or the whole
    file when it has no `== ... ==` sections (a recap pasted into a plain file)."""
    from dmbot.devtools.recap_run import review as rv

    _, parts = rv._sections(text)
    return parts.get(rv.S_PLAIN, "").strip() or text.strip()


def beats_from_file(raw: str) -> list[dict[str, Any]] | None:
    """The beat sheet in a saved `beats.json`, or None when it is not one."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, list) and all(isinstance(b, dict) for b in data) else None


def read_texts(recap_path: Path, beats_path: Path) -> tuple[str, str]:
    """The two files' text (read off the event loop by the caller)."""
    return (
        recap_path.read_text(encoding="utf-8-sig"),
        beats_path.read_text(encoding="utf-8-sig"),
    )


async def score_recap_file(
    recap_text: str, beats_text: str, musts: Sequence[str], client: Client, script: str = ""
) -> list[str]:
    """Score a recap that was written earlier, by a build that had no `--score`: its text
    (`recap_text`), judged against the beat sheet of a run of the SAME session (`beats_text`), so
    "before" and "after" are held to the same facts. No fact check is run. Counts only."""
    recap = recap_from_file(recap_text)
    beats = beats_from_file(beats_text)
    if not recap:
        return ["", "Must-haves: that file has no recap in it."]
    if beats is None:
        return ["", "Must-haves: the beats file is not a saved beat sheet (beats.json)."]
    return await score(recap, beats, musts, None, client, script)
