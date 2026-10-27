"""`recap_run --compare`: the same session written by two story models, read blind (#2575).

The beats are made once (by the first run) and the second writer starts from the very same beat
sheet, so the comparison is of the writers, not of two different beat sheets. Each draft goes
through the same checks and critics as a live recap. The drafts are written as `A.txt` and `B.txt`
with no provider or model name in them, and which is which goes in `key.txt`, a separate file the
reader opens only afterwards. Which writer is A is a coin toss. The terminal report labels each
draft A or B and prints its checks, tokens and cost, and names no provider or model. The second
run starts after the beats, so its numbers are the writing and the checks only; the first run's
also hold the beat sheet (said in the report). Files are private to the user; a recap holds the
DM's campaign, so **paste nothing from them into GitHub but the scores and counts.**

Operating notes (#2575). Run it on the server:
`python -m dmbot.devtools.recap_run --session GUILD CAMPAIGN SESSION_AT --compare --out DIR
--gemini-model NAME --paid-gemini-key --yes` (or `--transcript FILE --dm NAME`). `--claude-model`
sets the Claude writer (default: the creative model on file). It needs ANTHROPIC_API_KEY and
GEMINI_API_KEY. **The Gemini key must belong to a Google account with billing on:** on the free
tier Google may use what is sent, which is the session's text, and `--paid-gemini-key` is your
word that it is not. Neither client falls back to the other, so a mistyped model is an error. The
cost of each share is printed per draft; give `--price MODEL=IN,OUT` (dollars per million tokens)
for a model with no price on file. Gemini's price is not on file: take it from Google's pricing
page when it is needed (the price section of docs/costs.md waits for the owner's label).
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dmbot.ai import AIModels, AIModelTier, AnthropicClient, GeminiClient
from dmbot.devtools.recap_run import review as rv
from dmbot.recaps import pipeline as pl
from dmbot.recaps.pipeline import Client

if TYPE_CHECKING:
    from dmbot.devtools.recap_run.__main__ import Outcome

DRAFT_FILES = ("A.txt", "B.txt")
KEY_FILE = "key.txt"


@dataclass(frozen=True, slots=True)
class Writer:
    """One of the two story models: how it is shown in the key, and the clients that use it."""

    label: str  # for key.txt only, e.g. "Anthropic" and the tier it ran
    creative: Client
    formatting: Client
    models: tuple[str, str]  # (creative, formatting) model names, for the cost lookup
    reads: str = "beats"  # what it reads (#2590): the beat sheet or the cleaned transcript


def blind_order(pick: Callable[[int], int] = secrets.randbelow) -> tuple[int, int]:
    """Which writer is draft A: (0, 1) or (1, 0), by a coin that is not the same every time."""
    return (0, 1) if pick(2) == 0 else (1, 0)


def start_from(first: Outcome) -> tuple[str, dict[str, Any]]:
    """The state and job fields a second run starts with: the first run's checked beat sheet, its
    speakers and its split, at the state just after the beats were checked. Raises `ValueError`
    when the first run did not get that far."""
    job = first.job
    if not job.beats or job.state in ("queued", "beats"):
        raise ValueError("the first writer's run did not reach the beat sheet")
    return "checked", {
        "beats": list(job.beats),
        "speakers": tuple(job.speakers),
        "split": job.split,
    }


def draft_text(out: Outcome, *, voice_tags: bool) -> str:
    """The recap as a reader sees it, and nothing that names a provider or a model."""
    job = out.job
    return rv.recap_text(
        job.title,
        job.summary,
        list(job.housekeeping),
        list(job.music),
        voice_tags=voice_tags,
        names_given=True,
        to_confirm=list(job.to_confirm),
    )


def write_blind(
    folder: Path,
    outcomes: Sequence[Outcome],
    labels: Sequence[str],
    order: tuple[int, int],
    *,
    voice_tags: bool,
) -> list[str]:
    """`A.txt`, `B.txt` and, apart, `key.txt`. `order[i]` is which outcome is draft i."""
    rv.private_dir(folder)
    key: list[str] = []
    for slot, which in enumerate(order):
        text = draft_text(outcomes[which], voice_tags=voice_tags)
        rv.write_private(folder / DRAFT_FILES[slot], text)
        key.append(f"{'AB'[slot]} = {labels[which]}")
    rv.write_private(folder / KEY_FILE, "\n".join(key) + "\n")
    return key


def summary_lines(
    out: Outcome,
    letter: str,
    prices: dict[str, tuple[float, float]],
    checks: list[tuple[str, bool, str]],
) -> list[str]:
    """One draft's terminal report: state, every check, calls and tokens and the cost of each
    share. The rows are the same for both drafts and name no provider or model. Fixed words and
    counts; no text of the recap."""
    job = out.job
    rows = [
        f"== Draft {letter} ==",
        f"state: {job.state}" + (f" ({out.stopped})" if out.stopped else ""),
    ]
    rows += [
        f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" ({d})" if d else "") for name, ok, d in checks
    ]
    if job.grade:
        rows.append("  grader: " + ", ".join(f"{k} {v}" for k, v in job.grade.items()))
    rows += [
        f"  {line}" for line in rv.count_lines(job, out.counts.get("away_found"))
    ]  # the away check and the beat sheet (#2738)
    parts = [
        ("writer", out.creative),
        ("formatter", out.formatting),
        ("checks", out.careful),
        ("everything else", out.fast),
    ]
    total = 0.0
    priced = True
    for name, counted in parts:
        if counted is None:
            counted_calls, used, cost = 0, "in 0, out 0", "$0.000"
        else:
            price = prices.get(counted.model)
            dollars = (
                (counted.input_tokens * price[0] + counted.output_tokens * price[1]) / 1e6
                if price
                else None
            )
            priced = priced and dollars is not None
            total += dollars or 0.0
            counted_calls = counted.calls
            used = f"in {counted.input_tokens}, out {counted.output_tokens}"
            cost = f"${dollars:.3f}" if dollars is not None else "no price on file"
        rows.append(f"  {name}: {counted_calls} calls, {used}, {cost}")
    note = "" if priced else " (without the share with no price)"
    rows.append(f"  total: ${total:.3f}{note}")
    return rows


def writers_from_env(
    get: Callable[[str], str], models: AIModels, claude_model: str, gemini_model: str,
    *, register: Callable[[AnthropicClient], None],
    which: Sequence[str] = ("claude", "gemini"), reads: Sequence[str] = ("beats", "beats"),
) -> list[Writer] | None:  # fmt: skip
    """The two writers, each on its own client (so no fallback from one to the other hides which
    wrote a draft): `which` names them, "claude" or "gemini" (the default pair is one of each; two
    of the same kind compare what each reads, #2590), and `reads` what each reads, "beats" or
    "transcript". None, after a plain line, when a key is missing."""
    key, gemini_key = get("ANTHROPIC_API_KEY"), get("GEMINI_API_KEY")
    if not key or (not gemini_key and "gemini" in which):
        print("recap_run: --compare needs ANTHROPIC_API_KEY and GEMINI_API_KEY", file=sys.stderr)
        return None
    writers: list[Writer] = []
    for kind, saw in zip(which, reads, strict=True):
        if kind == "claude":
            client = AnthropicClient(key, replace(models, creative=claude_model), fallback=False)
            name = claude_model
            label = f"Anthropic {claude_model}"
        else:
            client = AnthropicClient(
                key, replace(models, creative=gemini_model),
                gemini=GeminiClient(gemini_key), fallback=False,
            )  # fmt: skip
            name = gemini_model
            label = f"Gemini {gemini_model}"
        register(client)
        writers.append(
            Writer(
                f"{label}, reads the {saw}", client.tier(AIModelTier.CREATIVE),
                client.tier(AIModelTier.FORMATTING), (name, models.formatting), saw,
            )
        )  # fmt: skip
    return writers


RunText = Callable[..., Awaitable["Outcome"]]


async def run_compare(
    run_text: RunText,
    inputs: pl.Inputs,
    fast: Client,
    careful: Client,
    consenting: Callable[[], Awaitable[Collection[int]]] | None,
    *,
    session_at: int,
    models: tuple[str, str],
    writers: list[Writer],
    folder: Path,
    prices: dict[str, tuple[float, float]],
    checks_of: Callable[[Outcome], list[tuple[str, bool, str]]],
    leaks: Callable[[Outcome], bool],
    voice_tags: bool,
    order: tuple[int, int] | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Both writers on the same beats. 0 when both drafts finished, 1 when one did not, 3 when a
    draft names something the DM keeps secret (nothing is written then)."""
    order = order or blind_order()
    for name in (*DRAFT_FILES, KEY_FILE):  # nothing of an earlier comparison is left to be mixed in
        (folder / name).unlink(missing_ok=True)
    # Draft A runs first whichever writer it is, so the beat sheet (made by the first run) is in
    # A's numbers whichever provider wrote A: the report never shows which is which.
    first_writer, second_writer = writers[order[0]], writers[order[1]]
    first = await run_text(
        inputs, fast, careful, consenting, session_at=session_at, models=models,
        creative=first_writer.creative, formatting=first_writer.formatting,
        creative_models=first_writer.models, critics=True, reads=first_writer.reads,
    )  # fmt: skip
    try:
        start = start_from(first)
    except ValueError as exc:
        emit(f"recap_run: {exc}; nothing to compare.")
        return 1
    second = await run_text(
        inputs, fast, careful, consenting, session_at=session_at, models=models,
        creative=second_writer.creative, formatting=second_writer.formatting,
        creative_models=second_writer.models, critics=True, start=start,
        reads=second_writer.reads,
    )  # fmt: skip
    drafts = (first, second)  # draft A, draft B
    if any(leaks(o) for o in drafts):
        emit("recap_run: a draft names something the DM keeps secret, so nothing is written.")
        return 3
    write_blind(
        folder,
        drafts,
        [writers[order[0]].label, writers[order[1]].label],
        (0, 1),
        voice_tags=voice_tags,
    )
    emit(
        f"Wrote {DRAFT_FILES[0]} and {DRAFT_FILES[1]} in {folder}, and which is which in "
        f"{KEY_FILE}. Read the drafts first; open the key after. Draft B starts from draft A's "
        "beat sheet, so the beat sheet is in A's numbers only (under 'everything else')."
    )
    for slot, outcome_ in enumerate(drafts):
        emit("")
        for line in summary_lines(outcome_, "AB"[slot], prices, checks_of(outcome_)):
            emit(line)
    return 0 if all(o.job.state == "ready" for o in drafts) else 1
