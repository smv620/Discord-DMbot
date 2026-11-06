"""The admin page's Costs figures (#3671, part 1): what each paid service has charged this
month, read from the service itself where it lets a read-only key do that.

Why so little is read: only Deepgram (the bill) and ElevenLabs (characters used) have a
read-only key that can be scoped to costs. Anthropic's cost API needs an Admin key that can
also manage the whole account, so it is never asked for ("No Anthropic Admin key, ever",
Supervisor, #3671); Speechmatics and Voyage show "not recorded yet" until part 2 (#3672)
counts DMbot's own calls. Income by plan needs a door that reads every person's plan, which
is a migration, so it says "not available yet" here rather than guess.

Keys are read from settings, sent only to their own service and never logged or returned; the
cache holds figures only.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

MONTH = re.compile(r"20[0-9]{2}-(0[1-9]|1[0-2])")
CACHE_SECONDS = 300  # a month's figures change slowly; asking again sooner re-sends the cache
# (service, month) -> (read at, row). Figures only, never a key.
Cache = dict[tuple[str, str], tuple[int, "Row"]]
Fetch = Callable[[str, Mapping[str, str]], Awaitable[Any]]

# What each service covers for DMbot, in plain words, and where its own cost page is.
# `how` says how the figure is found: "deepgram" and "elevenlabs" are read with a key,
# "not_recorded" waits for part 2, "link" is a fixed bill with no figure from the service.
SERVICES: tuple[dict[str, str], ...] = (
    {
        "id": "deepgram",
        "name": "Deepgram",
        "covers": "Turns the table's voices into text, live.",
        "link": "https://console.deepgram.com/billing",
        "how": "deepgram",
    },
    {
        "id": "speechmatics",
        "name": "Speechmatics",
        "covers": "A second speech-to-text company for voices Deepgram finds hard.",
        "link": "https://portal.speechmatics.com/usage/",
        "how": "not_recorded",
    },
    {
        "id": "anthropic-fast",
        "name": "Anthropic, fast model",
        "covers": "Quick answers at the table, such as rules questions.",
        "link": "https://platform.claude.com/settings/cost",
        "how": "not_recorded",
    },
    {
        "id": "anthropic-careful",
        "name": "Anthropic, careful model",
        "covers": "Everyday reading of sessions, notes and lore.",
        "link": "https://platform.claude.com/settings/cost",
        "how": "not_recorded",
    },
    {
        "id": "anthropic-deep",
        "name": "Anthropic, deep model",
        "covers": "The hardest work: recaps and untangling who is who.",
        "link": "https://platform.claude.com/settings/cost",
        "how": "not_recorded",
    },
    {
        "id": "voyage",
        "name": "Voyage",
        "covers": "Search by meaning on the Names page.",
        "link": "https://dashboard.voyageai.com/organization/usage",
        "how": "not_recorded",
    },
    {
        "id": "elevenlabs",
        "name": "ElevenLabs",
        "covers": "The voice that reads recaps aloud.",
        "link": "https://elevenlabs.io/app/subscription",
        "how": "elevenlabs",
    },
    {
        "id": "server",
        "name": "Server host",
        "covers": "The computer DMbot runs on.",
        "link": "https://www.hetzner.com/cloud",
        "how": "link",
    },
    {
        "id": "cloudflare",
        "name": "Cloudflare",
        "covers": "The website, its address and its picture storage.",
        "link": "https://dash.cloudflare.com/?to=/:account/billing",
        "how": "link",
    },
    {
        "id": "stripe",
        "name": "Stripe",
        "covers": "Takes payments. Its fees come out of each payment.",
        "link": "https://dashboard.stripe.com/balance/overview",
        "how": "link",
    },
)

# The plans the income table lists, in the owner's order.
PLANS = (
    ("free", "Free tier"),
    ("try-me", "Try It"),
    ("table", "Table"),
    ("two-table", "Two-table"),
    ("guild", "Guild"),
)


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    name: str
    covers: str
    link: str
    # "read" (a figure), "not_connected" (no key), "not_recorded", "link_only" or "unavailable".
    state: str
    amount_cents: int | None = None
    detail: str = ""
    read_at: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "covers": self.covers,
            "link": self.link,
            "state": self.state,
            "amount_cents": self.amount_cents,
            "detail": self.detail,
            "read_at": self.read_at,
        }


def this_month(now: int) -> str:
    return datetime.datetime.fromtimestamp(now, datetime.UTC).strftime("%Y-%m")


def month_range(month: str) -> tuple[str, str]:
    """First and last day of `month` (YYYY-MM) as YYYY-MM-DD."""
    year, mon = int(month[:4]), int(month[5:])
    first = datetime.date(year, mon, 1)
    nxt = datetime.date(year + (mon == 12), mon % 12 + 1, 1)
    return first.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat()


async def fetch_json(url: str, headers: Mapping[str, str]) -> Any:
    """GET `url` as JSON, ten seconds at most. Raises on any failure; callers say only
    "unavailable" and never repeat the error text, which could echo a key."""
    timeout = aiohttp.ClientTimeout(total=10, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as http, http.get(url, headers=headers) as r:
        r.raise_for_status()
        return await r.json()


def _row(
    svc: Mapping[str, str],
    state: str,
    *,
    amount_cents: int | None = None,
    detail: str = "",
    read_at: int | None = None,
) -> Row:
    return Row(
        svc["id"], svc["name"], svc["covers"], svc["link"], state, amount_cents, detail, read_at
    )


async def _deepgram(key: str, month: str, fetch: Fetch) -> tuple[int, str]:
    """Dollars Deepgram billed in `month`, as cents. Its billing breakdown is per project."""
    auth = {"Authorization": f"Token {key}"}
    projects = await fetch("https://api.deepgram.com/v1/projects", auth)
    first, last = month_range(month)
    cents = 0.0
    seen = 0
    for project in (projects.get("projects") or [])[:10]:
        pid = str(project.get("project_id", ""))
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", pid):
            continue
        body = await fetch(
            f"https://api.deepgram.com/v1/projects/{pid}/billing/breakdown?start={first}&end={last}",
            auth,
        )
        seen += 1
        for item in body.get("results") or []:
            cents += float(item.get("dollars", 0)) * 100
    if not seen:
        raise ValueError("no project")
    return round(cents), f"{seen} project{'s' if seen != 1 else ''}"


async def _elevenlabs(key: str, fetch: Fetch) -> str:
    """Characters used this billing period; ElevenLabs shows no dollars to a read-only key."""
    sub = await fetch("https://api.elevenlabs.io/v1/user/subscription", {"xi-api-key": key})
    used, limit = int(sub["character_count"]), int(sub["character_limit"])
    return f"{used:,} of {limit:,} characters used this period"


async def read_costs(
    *,
    month: str,
    now: int,
    deepgram_key: str,
    elevenlabs_key: str,
    cache: Cache,
    fetch: Fetch = fetch_json,
) -> dict[str, Any]:
    """Every service's row for `month`, each read on its own so one failure shows on its
    own row only. The figures are for the whole of DMbot (never one campaign)."""
    keys = {"deepgram": deepgram_key, "elevenlabs": elevenlabs_key}

    async def one(svc: Mapping[str, str]) -> Row:
        how = svc["how"]
        if how == "link":
            return _row(svc, "link_only", detail="No figure from this service: open its page.")
        if how == "not_recorded":
            return _row(
                svc,
                "not_recorded",
                detail="Not recorded yet. Open its page for the figure.",
            )
        key = keys[how]
        if not key:
            return _row(svc, "not_connected", detail="Not connected.")
        hit = cache.get((svc["id"], month))
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            if how == "deepgram":
                cents, detail = await _deepgram(key, month, fetch)
                row = _row(svc, "read", amount_cents=cents, detail=detail, read_at=now)
            else:
                row = _row(svc, "read", detail=await _elevenlabs(key, fetch), read_at=now)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            # Type only: the message could carry the address with the key's project in it.
            log.warning("costs: %s unavailable (%s)", svc["id"], type(exc).__name__)
            return _row(svc, "unavailable", detail="Couldn't be read just now. Try again.")
        cache[(svc["id"], month)] = (now, row)
        return row

    rows = await asyncio.gather(*(one(s) for s in SERVICES))
    known = sum(r.amount_cents or 0 for r in rows if r.state == "read")
    return {
        "month": month,
        "services": [r.as_json() for r in rows],
        "spent_cents": known,
        # Honest about the gaps: the total only holds the services that gave a figure.
        "counted": [r.id for r in rows if r.state == "read" and r.amount_cents is not None],
        "income": [
            {
                "plan": plan,
                "name": name,
                "state": "not_available",
                "detail": "Not available yet.",
            }
            for plan, name in PLANS
        ],
    }
