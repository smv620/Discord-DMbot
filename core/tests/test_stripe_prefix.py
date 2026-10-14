"""The one-off invoice-prefix helper (#1228): from subscriptions to customers."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.account_id import to_dmbot_id
from dmbot.web import stripe_prefix
from dmbot.web.stripe import StripeConfig, StripeProvider


class Fake:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(
        self, method: str, path: str, form: Any, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        self.calls.append((method, path, form))
        if method == "GET":
            return 200, self.pages.pop(0)
        return 200, {}


def sub(sub_id: str, customer: str, user: str | None) -> dict[str, Any]:
    return {
        "id": sub_id,
        "customer": customer,
        "metadata": {} if user is None else {"user_id": user},
    }


class Backfill(unittest.IsolatedAsyncioTestCase):
    async def test_each_customer_gets_its_dmbot_id_once(self) -> None:
        http = Fake(
            [
                {
                    "data": [sub("sub_1", "cus_a", "42"), sub("sub_2", "cus_a", "42")],
                    "has_more": True,
                },
                {
                    "data": [sub("sub_3", "cus_b", "36"), sub("sub_4", "cus_c", None)],
                    "has_more": False,
                },
            ]
        )
        provider = StripeProvider(
            StripeConfig(secret_key="sk_test_x", prices={}, extra_hours_price=""),
            b"s",
            transport=http,
        )
        done, skipped = await stripe_prefix.set_prefixes(provider, http)
        self.assertEqual((done, skipped), (2, 1))
        posts = [(p, f["invoice_prefix"]) for m, p, f in http.calls if m == "POST"]
        self.assertEqual(
            posts,
            [("/v1/customers/cus_a", to_dmbot_id(42)), ("/v1/customers/cus_b", "10")],
        )
        self.assertIn("starting_after=sub_2", http.calls[2][1])


if __name__ == "__main__":
    unittest.main()
