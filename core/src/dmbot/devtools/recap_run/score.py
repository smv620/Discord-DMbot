"""`recap_run --score` (#3328): per must-have, was it covered, missed, or not given by the session,
and how the draft did on the fact check. Counts and line numbers only, so a run can be posted on
GitHub. Every PR that changes the recap writer or its instructions carries this before and after,
run on the same fixed set of sessions (docs/RECAP_STANDARD.md).

The states come from the same check the critics loop uses (`recaps.coverage`), asked once of the
finished recap; nothing is written to any campaign.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def format_score(
    states: dict[int, str] | None, total: int, checks: Sequence[tuple[str, bool, str]]
) -> list[str]:
    """The report lines. `states` None: the check's answer could not be read."""
    passed = sum(1 for _, ok, _ in checks if ok)
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
    out.append(f"Fact check and the final checks: {passed} of {len(checks)} passed")
    return out


async def score(
    summary: str,
    sheet: Sequence[dict[str, Any]] | None,
    musts: Sequence[str],
    checks: Sequence[tuple[str, bool, str]],
    client: Client,
) -> list[str]:
    """Ask the coverage check about a finished recap and format the counts. A recap that is not
    ready has nothing to score: the caller does not ask."""
    wanted = must_list(musts)
    beats = bt.from_json(sheet) if sheet else []
    sentences = rd.sentences_of(summary)
    try:
        reply = await client.complete(
            covg.SYSTEM,
            covg.message(wanted, beats, sentences),
            max_tokens=covg.room(len(wanted)),
            schema=covg.SCHEMA,
        )
    except Exception as exc:  # the type only: never the text
        return ["", f"Must-haves: the coverage check failed ({type(exc).__name__})"]
    return format_score(covg.parse(reply.text, len(wanted)), len(wanted), checks)
