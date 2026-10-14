"""One-off: give the customers that already exist in Stripe their DMbot ID as invoice prefix
(#1228). New customers get it when their checkout is made; this covers the ones made before
(test mode so far). Run on the server by dev1:

    docker compose run --rm --no-deps -T web-api python -m dmbot.web.stripe_prefix

It lists subscriptions, whose metadata carries our Discord user id, works out each DMbot ID
and sets it on the subscription's customer. Safe to run again. Prints counts, never ids,
emails or keys.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from dmbot.account_id import to_dmbot_id
from dmbot.web.stripe import HttpTransport, StripeConfig, StripeProvider

PAGE = 100


async def set_prefixes(provider: StripeProvider, transport: Any) -> tuple[int, int]:
    """(customers updated, subscriptions skipped). `transport` is the provider's own."""
    done = skipped = 0
    seen: set[str] = set()
    after: str | None = None
    while True:
        form = {"status": "all", "limit": str(PAGE)}
        if after:
            form["starting_after"] = after
        status, answer = await transport("GET", "/v1/subscriptions?" + _query(form), None)
        if status != 200 or not isinstance(answer, dict):
            raise RuntimeError(f"Stripe answered {status} listing subscriptions")
        items = answer.get("data") or []
        for sub in items:
            customer = sub.get("customer")
            meta = sub.get("metadata") or {}
            try:
                dmbot_id = to_dmbot_id(int(meta["user_id"]))
            except (KeyError, ValueError, TypeError):
                skipped += 1
                continue
            if not isinstance(customer, str) or customer in seen:
                continue
            seen.add(customer)
            await provider.set_invoice_prefix(customer, dmbot_id)
            done += 1
        if not answer.get("has_more") or not items:
            return done, skipped
        after = items[-1].get("id")


def _query(form: dict[str, str]) -> str:
    return "&".join(f"{k}={v}" for k, v in form.items())


async def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        print("STRIPE_SECRET_KEY is not set to a Stripe secret key.", file=sys.stderr)
        return 1
    transport = HttpTransport(key)
    provider = StripeProvider(
        StripeConfig(secret_key=key, prices={}, extra_hours_price=""), b"x", transport=transport
    )
    try:
        done, skipped = await set_prefixes(provider, transport)
    finally:
        await transport.close()
    print(f"Invoice prefix set on {done} customers; {skipped} subscriptions had no DMbot user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
