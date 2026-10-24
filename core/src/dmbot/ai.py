"""DMbot's AI text calls (Anthropic's Messages API). The first use is turning a document
into a names list (`dmbot.memory.name_documents`). The key comes only from the server's
settings, is never logged and never shown (CLAUDE.md, API keys). Bring-your-own keys
per server come later (#50).

**Tiers, not models (#1006, owner decision 2026-10-10).** Every call names a tier: FAST
(Haiku: simple jobs), CAREFUL (Sonnet: house-rule changes) or DEEP (Opus: PlotBot, later).
The settings say which model each tier uses (`AI_MODEL_FAST`, `AI_MODEL_CAREFUL`,
`AI_MODEL_DEEP`, `AI_MODEL_CREATIVE`, `AI_MODEL_FORMATTING`); no other file names a model. If a
model is refused (not found, or no permission) the call goes to the next tier down, once logged
for that model. Running out of money never falls back. Each call logs one line: tier, model,
tokens, never content.
"""

from __future__ import annotations

import asyncio
import base64
import enum
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from dmbot.alerts import Problem, raise_alert

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AIModelTier(enum.Enum):
    FAST = "fast"  # simple jobs
    CAREFUL = "careful"  # house-rule changes
    DEEP = "deep"  # PlotBot (phase 5c); nothing uses it yet
    # The recap's two models (#2074, docs/PLAN.md "Two models, and style kept apart from content"):
    # a creative one writes it as plain text, a small one shapes the text into sections.
    CREATIVE = "creative"
    FORMATTING = "formatting"


# What a refused tier falls back to: the next one down.
REQUEST_TIMEOUT_S = 120  # one request on the fast tier
# The middle and strongest models write more slowly: a long answer (Find names on a big
# document) needs longer. The session's own limit is the longest of them.
# A background call may now write up to OUTPUT_FLOOR tokens (#2596). The middle and strongest models
# write about 40 to 80 tokens a second, so 16,000 tokens can take over four minutes: the slow tiers
# wait 8 minutes, the fast ones 3. A live table answer keeps the short wait it always had
# (`LIVE_TIMEOUT_S`), whatever its tier.
SLOW_TIER_TIMEOUT_S = 480
FAST_TIER_TIMEOUT_S = 180
LIVE_TIMEOUT_S = REQUEST_TIMEOUT_S
TIER_TIMEOUT_S = {
    AIModelTier.FAST: FAST_TIER_TIMEOUT_S,
    AIModelTier.CAREFUL: SLOW_TIER_TIMEOUT_S,
    AIModelTier.DEEP: SLOW_TIER_TIMEOUT_S,
    AIModelTier.CREATIVE: SLOW_TIER_TIMEOUT_S,
    AIModelTier.FORMATTING: FAST_TIER_TIMEOUT_S,
}

NEXT_DOWN = {
    AIModelTier.DEEP: AIModelTier.CAREFUL,
    AIModelTier.CAREFUL: AIModelTier.FAST,
    AIModelTier.CREATIVE: AIModelTier.CAREFUL,
    AIModelTier.FORMATTING: AIModelTier.FAST,
}


@dataclass(frozen=True, slots=True)
class Image:
    """One image sent with a request (#2450): its media type and bytes. Sent once and never
    stored; the bytes are not logged."""

    media_type: str
    data: bytes

    def __repr__(self) -> str:  # never the bytes
        return f"Image({self.media_type!r}, {len(self.data)} bytes)"


class Feature(enum.StrEnum):
    """The jobs that use the AI. Rules, cleaner, house rules and the rules-alert check are
    listed ahead of their first use, so each already has its tier."""

    NAMES = "names"  # Find names: a document into a names list
    NAMES_BACKGROUND = "names_background"  # the names in a DM's own campaign background (#2167)
    # Pass 1 of the same read (#2454): the whole text into a plain list; pass 2 is the job above.
    NAMES_BACKGROUND_READ = "names_background_read"
    TOPIC = "topic"  # the off-topic filter
    AUDIO_CHECK = "audio_check"  # is the audio bad, or just quiet talk
    SIDEBAR = "sidebar"  # quick answers for the DM
    RULES = "rules"  # rules checks and lookups
    CLEANER = "cleaner"  # transcript cleaning
    HOUSE_RULES = "house_rules"  # house-rule changes (by voice, typed, or from a file)
    SIDEBAR_RETRY = "sidebar_retry"  # a second try when the quick answer fails its checks
    RULES_CONFIRM = "rules_confirm"  # a rules alert's candidate, confirmed before the DM sees it
    SHEET = "sheet"  # a player's own sheet document read into the allow-listed fields (#1125)
    FIGHTBOT = "fightbot"  # a short reminder of a player's own options in combat (#1129)
    RECAP = "recap"  # the cinematic recap after a session (#1382); off by default
    RECAP_RETRY = "recap_retry"  # its one more try, when the first draft fails the checks
    RECAP_FEEDBACK = "recap_feedback"  # the DM's feedback on a recap, sorted into points (#2076)
    EXTRACTOR = "extractor"  # the story extractor after a session (#1363); off by default
    SUPPORT = "support"  # DMbot's helper answering a support ticket (#1431); the website's job
    PLAN_READER = "plan_reader"  # the story plan read from an adventure (#1714); the DM's press
    TIME_SUGGEST = "time_suggest"  # a time jump suggested from vague narration (#1758)
    RECAP_WRITE = "recap_write"  # the recap written as plain text by the creative model (#2074)
    RECAP_FORMAT = "recap_format"  # that text shaped into sections by the small model (#2074)
    RULE_CONFLICTS = "rule_conflicts"  # a saved house rule that disagrees with another (#2284)
    IMAGE = "image"  # an image posted in the table chat, described once (#2450)
    IMAGE_MAP = "image_map"  # a map's places and layout, read again by the careful model (#2450)


# How much a background call may write at least (#2596, the owner 2026-10-24: "the cutoffs are
# causing all kinds of problems"). Every call used to set its own hand-sized limit, estimated from
# the answer it expected, and an answer longer than the estimate was cut off: a structured answer
# could not be read, a step failed, and once the DM was told "found no new names" (#2454). The
# service charges for the tokens actually written, never for the limit, so a generous limit costs
# nothing and a tight one only guards against a runaway answer, which has not been the problem.
# A caller's own number is still its estimate (and is logged against what was written, to tune
# later); none goes below this floor, up to what the model can write.
OUTPUT_FLOOR = 16_000
# The jobs a person waits for at the table keep short answers, short by design ("Quick, short and
# on topic"): they get their own number times this, so a normal answer is never cut, and the
# short wait above.
LIVE_FEATURES = frozenset(
    {
        Feature.SIDEBAR.value,
        Feature.SIDEBAR_RETRY.value,
        Feature.FIGHTBOT.value,
        Feature.AUDIO_CHECK.value,
        Feature.TIME_SUGGEST.value,
    }
)
LIVE_MULTIPLE = 4

# The tier of each job. Nothing else chooses: a job names its tier here, never a model.
FEATURE_TIERS = {
    Feature.NAMES: AIModelTier.CAREFUL,  # its proposals go into the campaign's memory
    # Suggestions only, each checked against the text, and the DM answers every one (#2167).
    Feature.NAMES_BACKGROUND: AIModelTier.FAST,
    # The careful model reads the whole text once; the code checks everything it says (#2454).
    Feature.NAMES_BACKGROUND_READ: AIModelTier.CAREFUL,
    Feature.TOPIC: AIModelTier.FAST,
    Feature.AUDIO_CHECK: AIModelTier.FAST,
    Feature.SIDEBAR: AIModelTier.FAST,
    Feature.RULES: AIModelTier.FAST,
    Feature.CLEANER: AIModelTier.FAST,
    Feature.HOUSE_RULES: AIModelTier.CAREFUL,
    Feature.SIDEBAR_RETRY: AIModelTier.CAREFUL,
    Feature.RULES_CONFIRM: AIModelTier.CAREFUL,  # (rules alerts: FAST spots, CAREFUL confirms)
    Feature.SHEET: AIModelTier.CAREFUL,  # its numbers and names are checked, but only once
    Feature.FIGHTBOT: AIModelTier.FAST,  # a short reminder every couple of minutes at most
    # The default; DMBOT_STORY_EXTRACTOR_TIER picks it, because the measurement (#1282) decides.
    Feature.EXTRACTOR: AIModelTier.FAST,
    Feature.RECAP: AIModelTier.FAST,
    Feature.PLAN_READER: AIModelTier.CAREFUL,  # read once and kept; it must not make things up
    Feature.TIME_SUGGEST: AIModelTier.FAST,  # one short line, one small answer, a DM presses
    Feature.SUPPORT: AIModelTier.DEEP,  # a person is reading the answer, and it must not mislead
    Feature.RECAP_RETRY: AIModelTier.CAREFUL,
    Feature.RECAP_FEEDBACK: AIModelTier.FAST,  # one small answer a DM waits for
    Feature.RECAP_WRITE: AIModelTier.CREATIVE,
    Feature.RECAP_FORMAT: AIModelTier.FORMATTING,
    # Numbers only, each checked against what was shown, and the DM answers every one (#2284).
    Feature.RULE_CONFLICTS: AIModelTier.FAST,
    # A short description by the fast model; a map is read again by the careful one, since the
    # layout has to be detailed enough to draw (#2450).
    Feature.IMAGE: AIModelTier.FAST,
    Feature.IMAGE_MAP: AIModelTier.CAREFUL,
}

# The setting that picks each tier's model (named in the log when one isn't available).
TIER_SETTINGS = {
    AIModelTier.FAST: "AI_MODEL_FAST",
    AIModelTier.CAREFUL: "AI_MODEL_CAREFUL",
    AIModelTier.DEEP: "AI_MODEL_DEEP",
    AIModelTier.CREATIVE: "AI_MODEL_CREATIVE",
    AIModelTier.FORMATTING: "AI_MODEL_FORMATTING",
}
REFUSED_RETRY_S = 900.0  # a model that wasn't available is tried again after this long


@dataclass(frozen=True, slots=True)
class AIModels:
    """The model each tier uses."""

    fast: str
    careful: str
    deep: str
    creative: str = "claude-opus-5-5"
    formatting: str = "claude-haiku-5-5"

    def of(self, tier: AIModelTier) -> str:
        return {
            AIModelTier.FAST: self.fast,
            AIModelTier.CAREFUL: self.careful,
            AIModelTier.DEEP: self.deep,
            AIModelTier.CREATIVE: self.creative,
            AIModelTier.FORMATTING: self.formatting,
        }[tier]

    @classmethod
    def same(cls, model: str) -> AIModels:
        """One model for every tier (tests and tools)."""
        return cls(model, model, model, model, model)


DEFAULT_MODELS = AIModels(
    fast="claude-haiku-5-5",  # the owner moved the fast tier off Haiku 4.5 (#2152)
    careful="claude-sonnet-5-5",
    deep="claude-opus-5-5",
    creative="claude-opus-5-5",  # writes the recap as plain text (#2074)
    formatting="claude-haiku-5-5",  # shapes the text into sections; no word of it changes (#2074)
)
# The model the comparison tool tries (#2055); it is now also the fast tier's default (#2152). A job
# names a tier, so this and DEFAULT_MODELS are the only places the id is written.
FAST_CANDIDATE = "claude-haiku-5-5"
# The fast tier's model before #2152, the baseline the comparison tool measures against, and the
# id to set `AI_MODEL_FAST` back to if Haiku 5.5 does worse on the sidebar.
PREVIOUS_FAST = "claude-haiku-4-5-20251001"
CONNECT_TIMEOUT_S = 15

# List prices in dollars per million tokens, (input, output), base tokens, from
# https://platform.claude.com/docs/en/about-claude/pricing (Sonnet 5.5 checked 2026-10-15, Haiku 5.5
# 2026-10-19: its prompts over 100,000 tokens pay $0.50 and $2.50; DMbot's calls are far shorter;
# Opus 5.5 checked 2026-10-20).
# For the money caps on reading an adventure (`dmbot.memory.story_plan`). A model that is not here
# (an override in .env, or the strongest tier) is priced at DEFAULT_PRICE, high on purpose.
PRICE_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2, 10),
    "claude-haiku-4-5-20251001": (1, 5),
    "claude-haiku-5-5": (0.10, 0.50),  # the fast tier since #2152 (measured in #2055)
    "claude-opus-5-5": (4, 20),  # the recap's creative model (#2074)
}
DEFAULT_PRICE = (15, 75)

# The most each model can write in one answer, in tokens (the model table at
# https://platform.claude.com/docs/en/about-claude/models, Sonnet 5.5, Haiku 5.5 and Opus 5.5
# checked 2026-10-22). A job that sizes its answer's room reads this, never a number of its own;
# a model that is not here (an override in .env) gets the cautious default.
MAX_OUTPUT_TOKENS: dict[str, int] = {
    "claude-sonnet-5-5": 64_000,
    "claude-haiku-5-5": 64_000,
    "claude-opus-5-5": 64_000,
    "claude-haiku-4-5-20251001": 64_000,
}
DEFAULT_MAX_OUTPUT = 16_000


# What a Gemini model may write in one answer: its documented ceiling is lower than the Claude
# models', and asking for more is refused. Override with GEMINI_MAX_OUTPUT (owner-set, #2599).
GEMINI_MAX_OUTPUT = 8192


def max_output(model: str) -> int:
    if model.startswith("gemini"):
        try:
            return int(os.environ.get("GEMINI_MAX_OUTPUT", "") or GEMINI_MAX_OUTPUT)
        except ValueError:
            return GEMINI_MAX_OUTPUT
    return MAX_OUTPUT_TOKENS.get(model, DEFAULT_MAX_OUTPUT)


def answer_room(estimate: int, model: str, *, live: bool = False) -> int:
    """The output limit a call is sent with (#2596). A background call: its own estimate, but not
    below `OUTPUT_FLOOR` (as far as the model can write). A live table answer: its estimate times
    `LIVE_MULTIPLE`. Never below what the caller asked for, whatever the model's own limit."""
    if live:
        return estimate * LIVE_MULTIPLE
    return max(estimate, min(OUTPUT_FLOOR, max_output(model)))


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

    def record(self, input_tokens: int, output_tokens: int, feature: str | None = None) -> None: ...

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
    cache_write_tokens: int = 0  # input tokens written to the cache (charged a little extra)
    refused: bool = (
        False  # the service declined (stop reason "refusal"): the text is not the answer
    )
    stop: str = ""  # the service's own word for why it stopped (end_turn, max_tokens ...)


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

    def __init__(
        self,
        client: AnthropicClient,
        tier: AIModelTier,
        feature: str | None = None,
        *,
        cache_system: bool = False,
    ) -> None:
        self._client = client
        self.tier = tier
        self.feature = feature  # named in the log line and usage totals (#1176)
        self.cache_system = cache_system  # the system text is the same on every call (#1367)

    @property
    def model(self) -> str:
        """The model this tier uses now (a lower tier's if this one was refused)."""
        return self._client.model_for(self.tier)

    def __repr__(self) -> str:  # never the key
        return f"TierClient(tier={self.tier.value!r}, model={self.model!r})"

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = 8000,
        schema: dict[str, Any] | None = None,
        images: Sequence[Image] = (),
    ) -> Reply:
        """`schema`: ask for the answer as JSON of this shape (structured outputs, #2039).
        `images`: sent with the text, for a job that reads a picture (#2450)."""
        return await self._client.complete(
            system,
            text,
            tier=self.tier,
            max_tokens=max_tokens,
            feature=self.feature,
            cache_system=self.cache_system,
            schema=schema,
            images=images,
        )


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

    def tier(
        self, tier: AIModelTier, feature: Feature | None = None, *, cache_system: bool = False
    ) -> TierClient:
        """The client for one job. `feature` is only a label for the log line and the usage
        totals, so jobs that share a tier can be counted apart."""
        return TierClient(
            self, tier, feature.value if feature is not None else None, cache_system=cache_system
        )

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
        self,
        system: str,
        text: str,
        *,
        tier: AIModelTier,
        max_tokens: int = 8000,
        feature: str | None = None,
        cache_system: bool = False,
        schema: dict[str, Any] | None = None,
        images: Sequence[Image] = (),
    ) -> Reply:
        """One request on `tier`: `system` instructions, `text` as the user's message (after any
        `images`, #2450).
        Returns the answer's text, and whether it was cut off at `max_tokens`. If the
        tier's model is refused, the next tier down answers instead. Raises AIError in
        plain words (AIOutOfFunds never falls back)."""
        chain = self._chain(tier)
        unavailable: list[tuple[AIModelTier, str]] = []  # refused on this call (tier, model)
        live = feature in LIVE_FEATURES
        for position, (step, model) in enumerate(chain):
            lower = chain[position + 1] if position + 1 < len(chain) else None
            room = answer_room(max_tokens, model, live=live)
            try:
                reply = await self._request(
                    system,
                    text,
                    room,
                    step,
                    model,
                    asked=tier,
                    can_refuse=lower is not None,
                    feature=feature,
                    cache_system=cache_system,
                    schema=schema,
                    images=images,
                    live=live,
                )
            except _ModelRefused as refused:
                if lower is None:
                    raise AIError(FAILED) from None
                unavailable.append((step, model))
                log.debug("Model %s was refused (HTTP %s)", model, refused.status)
                continue
            self._refused.pop(model, None)  # it works (again)
            if reply.output_tokens > max_tokens:
                # Data for bringing the limits down later with real numbers (#2596): counts only.
                log.info(
                    "AI answer longer than the caller's estimate: feature=%s estimate=%d wrote=%d "
                    "room=%d",
                    feature or "-",
                    max_tokens,
                    reply.output_tokens,
                    room,
                )
            # A lower tier answering proves the key is fine, so the model above it really
            # isn't available to it. Said once for each model.
            for gone_tier, gone in unavailable:
                if gone not in self._said:
                    self._said.add(gone)
                    log.warning(
                        "AI model %s (%s jobs) isn't available to this key. Using %s instead "
                        "for now; it is tried again in %d minutes. To fix it, check %s in .env "
                        "or your Anthropic access.",
                        gone,
                        gone_tier.value,
                        model,
                        REFUSED_RETRY_S // 60,
                        TIER_SETTINGS[gone_tier],
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
        feature: str | None = None,
        cache_system: bool = False,
        schema: dict[str, Any] | None = None,
        images: Sequence[Image] = (),
        live: bool = False,
    ) -> Reply:
        # `live`: a table answer keeps the short wait (`LIVE_TIMEOUT_S`).
        # `images`: each goes in as an image block before the text, base64 (#2450).
        # `schema`: structured outputs (#2039). The Messages API takes `output_config.format`
        # with `type: json_schema`; no beta header, and every model in the tier chain supports
        # it. The answer text is then JSON of that shape (unless it was cut off or refused).
        # `cache_system`: ask the service to remember the system text between calls, which it
        # charges much less to read again. It only does so above a minimum length that depends on
        # the model, and otherwise just answers as usual; the log line's cache_read says if it did.
        content: Any = text
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": base64.b64encode(image.data).decode("ascii"),
                    },
                }
                for image in images
            ]
            content.append({"type": "text", "text": text})
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": (
                [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                if cache_system
                else system
            ),
            "messages": [{"role": "user", "content": content}],
        }
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            async with (
                asyncio.timeout(LIVE_TIMEOUT_S if live else TIER_TIMEOUT_S[tier]),
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
                        raise_alert(Problem.AI_OUT_OF_FUNDS)
                        raise AIOutOfFunds(OUT_OF_FUNDS)
                    if can_refuse and _model_unavailable(resp.status, reason):
                        raise _ModelRefused(resp.status)
                    if resp.status in (401, 403):
                        log.error("The AI service refused the key (HTTP %s)", resp.status)
                        raise_alert(Problem.AI_KEY_REFUSED)
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
            "AI call: tier=%s model=%s in=%s out=%s cache_read=%s cache_write=%s stop=%s%s%s",
            tier.value,
            model,
            count("input_tokens"),
            count("output_tokens"),
            count("cache_read_input_tokens"),
            count("cache_creation_input_tokens"),
            data.get("stop_reason"),
            f" asked={asked.value}" if asked is not tier else "",
            f" feature={feature}" if feature else "",
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
            cache_write_tokens=count("cache_creation_input_tokens"),
            refused=data.get("stop_reason") == "refusal",
            stop=data["stop_reason"] if isinstance(data.get("stop_reason"), str) else "",
        )
        if self._watch is not None:
            # `input_tokens` leaves out what the cache wrote or read. Count a write in full and
            # a read at a tenth (what it is charged about), so the spend watch does not undercount.
            billed = reply.input_tokens + reply.cache_write_tokens + reply.cache_read_tokens // 10
            self._watch.record(billed, reply.output_tokens, feature)
        return reply
