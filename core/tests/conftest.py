"""Test setup shared by every test module."""

import asyncio
import sys
from collections.abc import Iterator

import pytest

from dmbot import install

# psycopg's async mode needs a selector event loop; Windows defaults to another one.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]


@pytest.fixture(autouse=True)
def no_install_link() -> Iterator[None]:
    """Every test starts without an application ID, as the bot does before login."""
    install.configure(None)
    yield
    install.configure(None)
