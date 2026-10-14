"""One-off: give the customers that already exist in Stripe their DMbot ID as invoice prefix
(#1228). New customers get it when their checkout is made; this covers the ones made before
(test mode so far). Run on the server by dev1:

    docker compose run --rm --no-deps -T web-api python -m dmbot.web.stripe_prefix

It lists subscriptions, whose metadata carries our Discord user id, looks up that person's
stored DMbot ID (after an account move the two can differ, and the stored one is the
account's) and sets it on the subscription's customer. A customer that Stripe refuses is
counted and skipped, so one bad customer doesn't stop the rest. Safe to run again. Prints
counts only, never ids, emails or keys.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from dmbot.db import Database
from dmbot.web.payments import PaymentError
from dmbot.web.stripe import HttpTransport, StripeConfig, StripeProvider

log = logging.getLogger(__name__)
PAGE = 100

# A person's stored DMbot ID, or None if they have no account.
Lookup = Callable[[int], Awaitable[str | None]]


@dataclass(slots=True)
class Counts:
    set: int = 0
    skipped: int = 0  # no DMbot user on the subscription, or no account for them
    failed: int = 0  # Stripe refused this customer


async def set_prefixes(provider: StripeProvider, transport: Any, lookup: Lookup) -> Counts:
    counts = Counts()
    seen: set[str] = set()
    after: str | None = None
    while True:
        form = {"status": "all", "limit": str(PAGE)}
        if after:
            form["starting_after"] = after
        status, answer = await transport("GET", "/v1/subscriptions?" + urlencode(form), None)
        if status != 200 or not isinstance(answer, dict):
            raise RuntimeError(f"Stripe answered {status} listing subscriptions")
        items = answer.get("data") or []
        for sub in items:
            customer = sub.get("customer")
            meta = sub.get("metadata") or {}
            try:
                dmbot_id = await lookup(int(meta["user_id"]))
            except (KeyError, ValueError, TypeError):
                dmbot_id = None
            if dmbot_id is None or not isinstance(customer, str):
                counts.skipped += 1
                continue
            if customer in seen:
                continue
            seen.add(customer)
            try:
                await provider.set_invoice_prefix(customer, dmbot_id)
            except PaymentError:
                counts.failed += 1
                continue
            counts.set += 1
        if not answer.get("has_more") or not items:
            return counts
        after = items[-1].get("id")


async def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    url = os.environ.get("DATABASE_URL", "")
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")) or not url:
        print("STRIPE_SECRET_KEY and DATABASE_URL must be set.", file=sys.stderr)
        return 1
    db = await Database.open(url, migrate=False)
    transport = HttpTransport(key)
    config = StripeConfig(secret_key=key, prices={}, extra_hours_price="")
    provider = StripeProvider(config, b"x", transport=transport)

    async def lookup(user_id: int) -> str | None:
        async with db.user(user_id) as conn:
            cur = await conn.execute(
                "SELECT dmbot_id FROM web_users WHERE user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
        return None if row is None else str(row["dmbot_id"])

    try:
        counts = await set_prefixes(provider, transport, lookup)
    finally:
        await transport.close()
        await db.close()
    print(
        f"Invoice prefix set on {counts.set} customers; {counts.skipped} subscriptions skipped;"
        f" {counts.failed} customers Stripe refused."
    )
    return 0 if counts.failed == 0 else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(main()))
