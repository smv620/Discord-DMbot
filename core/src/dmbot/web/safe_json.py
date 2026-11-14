"""The website's API never sends a number a browser cannot hold (#4060).

A Discord id is a number above 2^53, and a browser (or any tool built on doubles) reads one with
its last digits changed. The API's own models send ids as strings; this is the net under them:
every JSON response is written with any integer above 2^53 - 1 as a string, whatever route made it
and whatever key it is under, so a new field cannot bring the bug back. A number that fits (a
count, a time) is untouched.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from dmbot.campaigns.snowflakes import stringify_unsafe


class SafeJSONResponse(JSONResponse):
    """A JSON response with every unsafe integer written as a string."""

    def render(self, content: Any) -> bytes:
        return super().render(stringify_unsafe(content))
