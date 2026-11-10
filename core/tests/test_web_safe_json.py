"""The website's API never sends a number a browser cannot hold (#4060): any integer above 2^53 - 1
in a JSON response is a string, whatever route made it; numbers that fit are untouched. Invented
ids."""

from __future__ import annotations

import json
import unittest
from typing import Any

import httpx

from dmbot.web.app import create_app
from dmbot.web.safe_json import SafeJSONResponse
from tests.pg import DatabaseTest
from tests.test_web_api import FakeDiscord, settings

SNOWFLAKE = 1234567890123456789


class TheResponseClass(unittest.TestCase):
    def test_a_big_number_anywhere_is_a_string_and_the_rest_is_untouched(self) -> None:
        body = SafeJSONResponse(
            {"id": SNOWFLAKE, "who": [SNOWFLAKE, 5], "deep": {"x": [{"y": SNOWFLAKE}]}, "at": 1_800}
        ).body
        self.assertEqual(
            json.loads(bytes(body)),
            {
                "id": str(SNOWFLAKE),
                "who": [str(SNOWFLAKE), 5],
                "deep": {"x": [{"y": str(SNOWFLAKE)}]},
                "at": 1_800,
            },
        )


class TheApp(DatabaseTest):
    async def test_the_app_answers_with_it_for_every_route(self) -> None:
        app = create_app(settings(), self.db, FakeDiscord(), clock=lambda: 1_800_000_000)

        @app.get("/_a_route_that_forgot")
        async def forgot() -> dict[str, Any]:
            return {"user_id": SNOWFLAKE, "count": 3}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        ) as client:
            got = await client.get("/_a_route_that_forgot")
        self.assertEqual(got.json(), {"user_id": str(SNOWFLAKE), "count": 3})
