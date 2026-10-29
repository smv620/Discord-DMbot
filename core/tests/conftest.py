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


@pytest.fixture(autouse=True)
def fresh_sheet_gate() -> Iterator[None]:
    """Every test starts with a full bucket and no pause on D&D Beyond reads (#1171), so one
    test's reads or 429 never hold back another's."""
    from dmbot.memory import sheets

    sheets.GATE = sheets._Gate()
    yield
    sheets.GATE = sheets._Gate()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """CI runs the suite as parts side by side (#1984, `tests/shard.py`); each keeps its files'
    tests. Without DMBOT_TEST_SHARD every test runs, as always."""
    from tests import shard

    chosen = shard.wanted()
    if chosen is None:
        return
    part, total = chosen
    items[:] = [i for i in items if shard.part_of(str(i.path.name), total) == part]
