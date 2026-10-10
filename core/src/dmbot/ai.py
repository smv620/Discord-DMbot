"""DMbot's AI text calls (Anthropic's Messages API). The first use is turning a document
into a names list (`dmbot.memory.name_documents`). The key comes only from the server's
settings, is never logged and never shown (CLAUDE.md, API keys). Bring-your-own keys
per server come later (#50).

**Tiers, not models (#1006, owner decision 2026-10-10).** Every call names a tier: FAST
(Haiku: simple jobs), CAREFUL (Sonnet: house-rule changes) or DEEP (Opus: PlotBot, later).
The settings say which model each tier uses (`AI_MODEL_FAST`, `AI_MODEL_CAREFUL`,
`AI_MODEL_DEEP`); no other file names a model. If a model is refused (not found, or no
permission) the call goes to the next tier down, once logged for that model. Running out
of money never falls back. Each call logs one line: tier, model, tokens, never content.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AIModelTier(enum.Enum):
    FAST = "fast"  # simple jobs
    CAREFUL = "careful"  # house-rule changes
    DEEP = "deep"  # PlotBot (phase 5c); nothing uses it yet


# What a refused tier falls back to: the next one down.
REQUEST_TIMEOUT_S = 120  # one request on the fast tier
# The middle and strongest models write more slowly: a long answer (Find names on a big
# document) needs longer. The session's own limit is the longest of them.
SLOW_TIER_TIMEOUT_S = 240
TIER_TIMEOUT_S = {
    AIModelTier.FAST: REQUEST_TIMEOUT_S,
    AIModelTier.CAREFUL: SLOW_TIER_TIMEOUT_S,
    AIModelTier.DEEP: SLOW_TIER_TIMEOUT_S,
}

NEXT_DOWN = {AIModelTier.DEEP: AIModelTier.CAREFUL, AIModelTier.CAREFUL: AIModelTier.FAST}


class Feature(enum.StrEnum):
    """The jobs that use the AI. Rules, cleaner, house rules and the rules-alert check are
    listed ahead of their first use, so each already has its tier."""

    NAMES = "names"  # Find names: a document into a names list
    TOPIC = "topic"  # the off-topic filter
    AUDIO_CHECK = "audio_check"  # is the audio bad, or just quiet talk
    SIDEBAR = "sidebar"  # quick answers for the DM
    RULES = "rules"  # rules checks and lookups
    CLEANER = "cleaner"  # transcript cleaning
    HOUSE_RULES = "house_rules"  # house-rule changes (by voice, typed, or from a file)
    SIDEBAR_RETRY = "sidebar_retry"  # a second try when the quick answer fails its checks
    RULES_CONFIRM = "rules_confirm"  # a rules alert's candidate, confirmed before the DM sees it


# The tier of each job. Nothing else chooses: a job names its tier here, never a model.
FEATURE_TIERS = {
    Feature.NAMES: AIModelTier.CAREFUL,  # its proposals go into the campaign's memory
    Feature.TOPIC: AIModelTier.FAST,
    Feature.AUDIO_CHECK: AIModelTier.FAST,
    Feature.SIDEBAR: AIModelTier.FAST,
    Feature.RULES: AIModelTier.FAST,
    Feature.CLEANER: AIModelTier.FAST,
    Feature.HOUSE_RULES: AIModelTier.CAREFUL,
    Feature.SIDEBAR_RETRY: AIModelTier.CAREFUL,
    Feature.RULES_CONFIRM: AIModelTier.CAREFUL,  # (rules alerts: FAST spots, CAREFUL confirms)
}

# The setting that picks each tier's model (named in the log when one isn't available).
TIER_SETTINGS = {
    AIModelTier.FAST: "AI_MODEL_FAST",
    AIModelTier.CAREFUL: "AI_MODEL_CAREFUL",
    AIModelTier.DEEP: "AI_MODEL_DEEP",
}
REFUSED_RETRY_S = 900.0  # a model that wasn't available is tried again after this long


@dataclass(frozen=True, slots=True)
class AIModels:
    """The model each tier uses."""

    fast: str
    careful: str
    deep: str

    def of(self, tier: AIModelTier) -> str:
        return {
            AIModelTier.FAST: self.fast,
            AIModelTier.CAREFUL: self.careful,
            AIModelTier.DEEP: self.deep,
        }[tier]

    @classmethod
    def same(cls, model: str) -> AIModels:
        """One model for every tier (tests and tools)."""
        return cls(model, model, model)


DEFAULT_MODELS = AIModels(
    fast="claude-haiku-4-5-20251001",
    careful="claude-sonnet-5-5",
    deep="claude-opus-5-5",
)
CONNECT_TIMEOUT_S = 15


BUSY = (
    "DMbot couldn't reach the AI service, or it's busy. Nothing was added. In a minute, run "
    "/dmbot names with the file again."
)
FAILED = "The AI service couldn't do that. Nothing was added. Try again later."


class AIError(Exception):
    """In plain words for the DM; the cause is in the log."""


OUT_OF_FUNDS = (
    "DMbot's AI is paused right now (its account needs topping up). Nothing was changed. "
    "You can keep playing; the AI features come back once it's fixed."
)


class AIOutOfFunds(AIError):
    """The AI account has no credit left or has reached its spend limit. The DM is told
    plainly; the people who can fix it are messaged (dmbot.ai_watch)."""


class Watcher(Protocol):
    """What the client reports to (`dmbot.ai_watch.AIWatch`)."""

    def record(self, input_tokens: int, output_tokens: int) -> None: ...

    def out_of_funds(self) -> None: ...


# Anthropic's refusals for want of money. Docs: https://docs.anthropic.com/en/api/errors
# A "billing_error" is the API's own type for it (HTTP 402); a low balance or a workspace
# spend limit arrives as a 400 "invalid_request_error" whose message says so. The statuses
# 400, 403 and 429 are accepted defensively. Only these exact wordings count (not a bare
# "billing" or "usage"), so other errors are not mistaken for it and nobody is paged for
# nothing.
_FUNDS_WORDS = re.compile(
    r"credit balance is too low|reached your specified workspace api usage limits?"
    r"|reached your (monthly )?(spend|spending) limit",
    re.IGNORECASE,
)


def is_out_of_funds(status: int, body_text: str) -> bool:
    """Is this error response the account being out of funds or at its limit?"""
    if status == 402:
        return True
    if status not in (400, 403, 429):
        return False
    try:
        error = json.loads(body_text).get("error", {})
        kind, message = str(error.get("type", "")), str(error.get("message", ""))
    except (ValueError, AttributeError):
        return False
    return kind == "billing_error" or bool(_FUNDS_WORDS.search(message))


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    cut: bool  # stopped at the length limit: the end is missing
    input_tokens: int = 0  # as the AI service counted them (what it charges for)
    output_tokens: int = 0
    model: str = ""  # the model that answered (after any fallback)
    cache_read_tokens: int = 0  # input tokens read from the cache


def _model_unavailable(status: int, body_text: str) -> bool:
    """Is this error response the service saying the model isn't there (404) or isn't
    allowed for this key (a 403 `permission_error`)? Any other 403 (a blocked request, a
    proxy's page) is not taken to mean the model."""
    if status == 404:
        return True
    if status != 403:
        return False
    try:
        error = json.loads(body_text).get("error", {})
        return str(error.get("type", "")) == "permission_error"
    except (ValueError, AttributeError):
        return False


def step_of_tier(models: AIModels, model: str) -> AIModelTier:
    """The highest tier that uses this model."""
    return next((t for t in AIModelTier if models.of(t) == model), AIModelTier.FAST)


def step_of(models: AIModels, model: str) -> str:
    return step_of_tier(models, model).value


class _ModelRefused(Exception):
    """The service refused this model (not found, or no permission), not the request."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class TierClient:
    """The client one job uses: always the same tier. This is what a feature holds, so its
    calls name a tier by being made on it, and no feature names a model."""

    def __init__(self, client: AnthropicClient, tier: AIModelTier) -> None:
        self._client = client
        self.tier = tier

    @property
    def model(self) -> str:
        """The model this tier uses now (a lower tier's if this one was refused)."""
        return self._client.model_for(self.tier)

    def __repr__(self) -> str:  # never the key
        return f"TierClient(tier={self.tier.value!r}, model={self.model!r})"

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
        return await self._client.complete(system, text, tier=self.tier, max_tokens=max_tokens)


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        models: AIModels = DEFAULT_MODELS,
        session: aiohttp.ClientSession | None = None,
        watch: Watcher | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = api_key
        self.models = models
        self._session = session
        self._watch = watch
        self._clock = clock
        # Models the service said weren't available, and when: skipped for a while (a model
        # that gets fixed is tried again), never for good.
        self._refused: dict[str, float] = {}
        self._said: set[str] = set()  # models already named in a log line (once each)

    def __repr__(self) -> str:  # never the key
        return f"AnthropicClient(models={self.models!r})"

    def tier(self, tier: AIModelTier) -> TierClient:
        return TierClient(self, tier)

    def _chain(self, tier: AIModelTier) -> list[tuple[AIModelTier, str]]:
        """The tier, then each one below it, without repeats and without a model already
        refused (unless nothing else is left)."""
        steps: list[tuple[AIModelTier, str]] = []
        step: AIModelTier | None = tier
        while step is not None:
            model = self.models.of(step)
            if all(model != known for _, known in steps):
                steps.append((step, model))
            step = NEXT_DOWN.get(step)
        now = self._clock()
        usable = [
            (t, m)
            for t, m in steps
            if m not in self._refused or now - self._refused[m] >= REFUSED_RETRY_S
        ]
        return usable or steps[-1:]

    def model_for(self, tier: AIModelTier) -> str:
        return self._chain(tier)[0][1]

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=SLOW_TIER_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def complete(
        self, system: str, text: str, *, tier: AIModelTier, max_tokens: int = 8000
    ) -> Reply:
        """One request on `tier`: `system` instructions, `text` as the user's message.
        Returns the answer's text, and whether it was cut off at `max_tokens`. If the
        tier's model is refused, the next tier down answers instead. Raises AIError in
        plain words (AIOutOfFunds never falls back)."""
        chain = self._chain(tier)
        unavailable: list[str] = []  # refused on this call: only noted once a lower tier answers
        for position, (step, model) in enumerate(chain):
            lower = chain[position + 1] if position + 1 < len(chain) else None
            try:
                reply = await self._request(
                    system, text, max_tokens, step, model, asked=tier, can_refuse=lower is not None
                )
            except _ModelRefused as refused:
                if lower is None:
                    raise AIError(FAILED) from None
                unavailable.append(model)
                log.debug("Model %s was refused (HTTP %s)", model, refused.status)
                continue
            self._refused.pop(model, None)  # it works (again)
            # A lower tier answering proves the key is fine, so the model above it really
            # isn't available to it. Said once for each model.
            for gone in unavailable:
                if gone not in self._said:
                    self._said.add(gone)
                    log.warning(
                        "AI model %s (%s jobs) isn't available to this key. Using %s instead "
                        "for now; it is tried again in %d minutes. To fix it, check %s in .env "
                        "or your Anthropic access.",
                        gone,
                        step_of(self.models, gone),
                        model,
                        REFUSED_RETRY_S // 60,
                        TIER_SETTINGS[step_of_tier(self.models, gone)],
                    )
                self._refused[gone] = self._clock()
            return reply
        raise AIError(FAILED)  # (the last step never refuses: it raises its own error)

    async def _request(
        self,
        system: str,
        text: str,
        max_tokens: int,
        tier: AIModelTier,
        model: str,
        *,
        asked: AIModelTier,
        can_refuse: bool,
    ) -> Reply:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            async with (
                asyncio.timeout(TIER_TIMEOUT_S[tier]),
                self._get_session().post(API_URL, json=body, headers=headers) as resp,
            ):
                if resp.status != 200:
                    # Read the reason once: out of funds is told apart from a bad key, a
                    # rate limit or a bad request by what the service says (never the key).
                    reason = (await resp.text())[:2000]
                    if is_out_of_funds(resp.status, reason):
                        log.error("The AI service says the account is out of funds or at its limit")
                        if self._watch is not None:
                            self._watch.out_of_funds()
                        raise AIOutOfFunds(OUT_OF_FUNDS)
                    if can_refuse and _model_unavailable(resp.status, reason):
                        raise _ModelRefused(resp.status)
                    if resp.status in (401, 403):
                        log.error("The AI service refused the key (HTTP %s)", resp.status)
                        raise AIError(
                            "The AI service didn't accept DMbot's key. Nothing was added. Ask "
                            "whoever runs DMbot to check its Anthropic key."
                        )
                    if resp.status in (429, 529) or resp.status >= 500:
                        log.warning("The AI service is busy (HTTP %s)", resp.status)
                        raise AIError(BUSY)
                    log.error("AI request failed (HTTP %s): %s", resp.status, reason[:500])
                    raise AIError(FAILED)
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:  # ValueError: bad JSON
            log.warning("AI request failed: %s", type(exc).__name__)
            raise AIError(BUSY) from exc
        if not isinstance(data, dict) or not isinstance(data.get("content", []), list):
            log.error("AI answer wasn't in the expected shape")
            raise AIError(FAILED)
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        def count(name: str) -> int:
            value = usage.get(name)
            return value if type(value) is int else 0  # not a bool

        # One line for each call, for watching the cost: counts only, never the text.
        log.info(
            "AI call: tier=%s model=%s in=%s out=%s cache_read=%s stop=%s%s",
            tier.value,
            model,
            count("input_tokens"),
            count("output_tokens"),
            count("cache_read_input_tokens"),
            data.get("stop_reason"),
            f" asked={asked.value}" if asked is not tier else "",
        )
        blocks = [b for b in data.get("content", []) if isinstance(b, dict)]
        answer = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        reply = Reply(
            answer,
            cut=data.get("stop_reason") == "max_tokens",
            input_tokens=count("input_tokens"),
            output_tokens=count("output_tokens"),
            model=model,
            cache_read_tokens=count("cache_read_input_tokens"),
        )
        if self._watch is not None:
            self._watch.record(reply.input_tokens, reply.output_tokens)
        return reply
