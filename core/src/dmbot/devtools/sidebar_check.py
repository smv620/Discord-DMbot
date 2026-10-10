"""Run the sidebar's table questions against the real model and print the answers (#934).

CI runs the same questions with a fake AI (`tests/test_sidebar_answer.py`); this is the one
check with the real thing, run by hand on the server before the sidebar goes live, so
Supervisor can read the answers and the times:

    ANTHROPIC_API_KEY=... python -m dmbot.devtools.sidebar_check [path/to/sidebar_brevity_cases.py]

It prints each question, DMbot's answer and how long it took, then whether the answer kept to
the length rules. The key is read from the environment and never printed. It uses a made-up
campaign (one sample house rule, no names) and spends a few cents of the operator's AI money.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from dmbot.ai import FEATURE_TIERS, AnthropicClient
from dmbot.campaigns.models import Campaign
from dmbot.config import parse_ai_models
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import brevity
from dmbot.sidebar.answer import Sidebar

DEFAULT_CASES = Path("tests/sidebar_brevity_cases.py")
SAMPLE_HOUSE_RULE = HouseRule(
    3, "check", "Criticals deal maximum damage plus the roll.", "Critical hits", None, None, 1, 0, 0
)


def load_cases(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("sidebar_brevity_cases", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Can't read {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_campaign() -> Campaign:
    return Campaign(
        id="check",
        guild_id=1,
        name="Sample",
        created_at=0,
        last_played_at=None,
        target_ruleset="2024",
        fallback_ruleset="2014",
        optional_rules_default=True,
        dm_user_ids=frozenset({1}),
        dm_screen_channel_id=None,
        last_voice_channel_id=None,
        dm_screen_visibility="peek",
        owner_user_id=1,
    )


async def run(path: Path) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise SystemExit("Set ANTHROPIC_API_KEY (the server's key) to run this.")
    cases = load_cases(path).CASES
    models, _ = parse_ai_models(lambda name: os.environ.get(name, ""))
    ai_client = AnthropicClient(key, models)
    client = ai_client.tier(FEATURE_TIERS["sidebar"])
    print(f"Model: {client.model} (tier {client.tier.value})\n")

    async def gate(campaign: Campaign, user: int) -> str | None:
        return None

    async def houses(campaign: Campaign) -> Sequence[HouseRule]:
        return [SAMPLE_HOUSE_RULE]

    async def names(campaign: Campaign) -> None:
        return None

    sidebar = Sidebar(client, srd(), gate=gate, houses=houses, names=names)
    failed = 0
    try:
        for number, case in enumerate(cases, start=1):
            started = time.monotonic()
            got = await sidebar.answer(sample_campaign(), case.question, asker_id=1)
            took = time.monotonic() - started
            body = got.text.rsplit(" (", 1)[0] if got.text.endswith(")") else got.text
            ok = brevity.within_limit(body) or len(got.parts) > 1
            # What the answer must and must not say (#992): the words are the case's own.
            lowered = got.text.lower()
            missing = bool(case.must_any) and not any(w.lower() in lowered for w in case.must_any)
            banned = [w for w in case.must_not if w.lower() in lowered]
            failed += not ok or missing or bool(banned)
            verdict = "ok" if ok else "TOO LONG"
            if missing:
                verdict += f", MISSING one of {list(case.must_any)}"
            if banned:
                verdict += f", MUST NOT SAY {banned}"
            print(f"{number:2}. {case.question}")
            print(f"    → {got.text}")
            print(
                f"    {took:.1f} s, {len(got.text)} characters, {verdict}, answered by {got.model}"
            )
    finally:
        await ai_client.close()
    print(f"\n{len(cases) - failed} of {len(cases)} passed (length, must-say and must-not-say).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CASES)))
