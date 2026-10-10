"""The cost report as plain words (docs/costs.md), from the measured sessions (#945)."""

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence

from dmbot.devtools.costs import model, prices
from dmbot.transcription.base import MIN_UTTERANCE_S

OUTPUT_TOKENS_PER_LABEL = 3  # the same as measure.OUTPUT_TOKENS_PER_LABEL (a test checks)


def money(value: float) -> str:
    return f"${value:.3f}" if value < 1 else f"${value:.2f}"


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}"


def render(
    sessions: Sequence[model.Session],
    sidebar: model.Usage,
    questions: int,
    cases: Sequence[model.PerHour],
    args: argparse.Namespace,
    audio_check: model.Usage | None = None,
    *,
    today: dt.date | None = None,
) -> str:
    today = today or dt.date.today()
    measured, total_minutes = model.per_feature(sessions)
    table_minutes = sum(s.table_s for s in sessions) / 60
    density = total_minutes / table_minutes if table_minutes else 0.0
    out: list[str] = [
        "# What a table costs DMbot to serve",
        "",
        f"Made by `python -m dmbot.devtools.replay --measure` on {today.isoformat()} (#945). "
        "It needs no key and no network. Run it again after a price or a feature changes.",
        "",
        "## Cost per table-hour",
        "",
        "Dollars for one hour of a table, by how much speech is sent to speech-to-text in that "
        "hour. The three cases are the 36, 48 and 60 speech-minutes an hour that PLAN.md "
        "assumed before; no real table has been measured yet (run 8 will). 60 is a ceiling: "
        f"every minute of the hour spoken. The twin's own sessions are {density:.0%} speech "
        f"(about {density * 60:.0f} minutes an hour), which only shows the typical case is "
        "not far off. The "
        "AI parts use ratios measured on the twin (below), scaled to each case, and "
        "everything is priced at the providers' list prices.",
        "",
        "| Case | Speech sent (min/hour) | Speech-to-text | Off-topic filter | DM sidebar "
        "| Hosting | Total |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        hosting = money(case.hosting) if case.hosting is not None else "not included"
        out.append(
            f"| {case.case} | {case.speech_minutes:.0f} | {money(case.stt)} "
            f"| {money(case.ai.get(model.FILTER, 0.0))} "
            f"| {money(case.ai.get(model.SIDEBAR, 0.0))} | {hosting} "
            f"| **{money(case.total)}** |"
        )
    out += [""]
    if cases and cases[0].hosting is None:
        low, high = cases[0], cases[-1]
        out += [
            "Hosting is not in the totals: its monthly cost is not something the twin can "
            "measure and nobody has given it yet. To add it, divide the server's monthly cost "
            "by the table-hours served a month (run with `--hosting-monthly DOLLARS "
            "--table-hours-per-month HOURS`); for example, every $10 a month spread over "
            "100 table-hours adds $0.10 to each table-hour. Without hosting a table-hour "
            f"costs {money(low.total_without_hosting)} to {money(high.total_without_hosting)}.",
            "",
        ]
    out += [
        "## How to read it",
        "",
        "- Speech-to-text is the biggest part, and it is the price times the speech-minutes a "
        "real table sends. The twin's recordings are under a minute each, so they confirm "
        "*what* is sent (the pieces and the minimum length), not *how much* a real evening "
        "sends. That number is an assumption until run 8.",
        "- The AI parts come from measured ratios (calls per speech-minute for the filter, "
        "tokens per question for the sidebar) at those assumed amounts.",
        "- Every figure is at list prices, so a discount would only lower it.",
        "",
        "## What was measured",
        "",
        "| Session | Table time (min) | Speech sent (min) | Lines | Filter calls | "
        "Filter tokens in / out | How |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in sessions:
        use = s.ai.get(model.FILTER, model.Usage())
        how = "recording, cut as core cuts it" if s.measured_audio else "script, timed by its words"
        out.append(
            f"| {s.name} | {minutes(s.table_s)} | {minutes(s.speech_s)} | {s.lines} "
            f"| {use.calls:.0f} | {use.input_tokens:.0f} / {use.output_tokens:.0f} | {how} |"
        )
    filter_use = measured.get(model.FILTER, model.Usage())
    out += [
        "",
        f"- **Speech sent** is what Deepgram bills: the pieces core would send, without those "
        f"under {MIN_UTTERANCE_S} s. The twin sent {total_minutes:.1f} speech-minutes in "
        f"{table_minutes:.1f} minutes of table time ({density:.0%}); a real table is "
        "assumed to be quieter, see the cases above.",
        f"- **Off-topic filter:** {filter_use.calls:.0f} calls for {total_minutes:.1f} "
        f"speech-minutes, so {filter_use.calls / total_minutes if total_minutes else 0:.2f} "
        "calls per speech-minute, scaled to each case. No campaign names were loaded, so no "
        "line was spared for naming something; a real campaign asks about slightly fewer.",
        f"- **DM sidebar:** the real engine on its {questions} table questions: "
        f"{sidebar.calls:.2f} AI calls a question (retries included), "
        f"{sidebar.input_tokens:.0f} tokens in and {sidebar.output_tokens:.0f} out, times "
        f"{model.SIDEBAR_QUESTIONS_PER_HOUR:.0f} questions an hour. The campaign was a sample "
        "with one house rule and no names; a real campaign's names and house rules make the "
        "question a little bigger.",
        "",
        "## Prices used (list prices, checked)",
        "",
        "| What | Price | Source | Checked |",
        "|---|---|---|---|",
    ]
    stt = prices.DEEPGRAM_PRERECORDED
    out.append(
        f"| Deepgram Nova-3 speech-to-text | ${stt.dollars} {stt.unit} | {stt.source} "
        f"| {stt.checked} |"
    )
    for name, (price_in, price_out) in prices.ANTHROPIC_PER_MILLION.items():
        out.append(
            f"| {name}, input | ${price_in.dollars} {price_in.unit} | {price_in.source} "
            f"| {price_in.checked} |"
        )
        out.append(
            f"| {name}, output | ${price_out.dollars} {price_out.unit} | {price_out.source} "
            f"| {price_out.checked} |"
        )
    out += [
        "",
        "No discount, credit or volume plan is counted. Core sends each piece of speech as a "
        "short file, so the pre-recorded price applies, not the streaming one "
        f"(${prices.DEEPGRAM_STREAMING.dollars} a minute).",
        "",
        "## Assumptions",
        "",
        "- **How much a real table talks:** "
        + ", ".join(f"{k} {v:.0f}" for k, v in model.SPEECH_MINUTES_PER_HOUR.items())
        + " speech-minutes sent per table-hour. That is the range PLAN.md assumed before "
        "(about 36 to 60); nothing has measured a real table yet.",
        f"- **Sidebar use:** {model.SIDEBAR_QUESTIONS_PER_HOUR:.0f} questions an hour, a guess.",
        "- **Tokens** are counted by size (about 3.5 characters each), not by the AI service, "
        "so they are estimates. The AI is a small part of the total; the speech-to-text is "
        "most of it.",
        "- **No AI at all** (checked in the code): the Transcript Cleaner, the after-session "
        "name scan and the rules cards. Only four things ask the AI: the off-topic filter, "
        "the DM sidebar, the audio check and reading a names file.",
        "- **Not counted:** reading a names file (the DM does it on purpose, now and then), "
        "payments, the website, backups, bandwidth.",
        *_audio_check_lines(audio_check),
        "- **Estimates inside the measure:** the filter's windows are timed by when each line "
        "was said (live, by when its text arrived, a little later) and its short answers are "
        f"counted at {OUTPUT_TOKENS_PER_LABEL} tokens a line; it does not model the filter "
        "resting after AI failures. The sidebar's answers come from the test cases' prepared "
        "replies, so its output tokens are the cases', not a real model's.",
        "- **Hosting** is the server's monthly cost split over the table-hours a month; it is "
        "an input, not a measure.",
        "",
        "## What run 8 should confirm",
        "",
        "1. Speech-minutes sent per table-hour at a real table, from the end-of-session line "
        '"Listened X min, sent Y min of speech" (the log). This is the biggest number here.',
        "2. The AI tokens the service really counted, from the usage line in the log, against "
        "the estimates above.",
        "3. How many sidebar questions a DM really asks in an hour (once it is on).",
        "4. The server's real monthly cost and the table-hours it serves a month.",
        "",
    ]
    return "\n".join(out)


def _audio_check_lines(usage: model.Usage | None) -> list[str]:
    if usage is None:
        return []
    return [
        "- **Audio check ceiling:** it asks the AI only while a speaker's audio is breaking up, "
        f"at most once a minute for each speaker: {usage.calls:.0f} calls an hour at the very "
        f"most, about {money(model.ai_dollars(usage))} an hour for that speaker. Normal play "
        "asks for none, so it is left out of the totals."
    ]
