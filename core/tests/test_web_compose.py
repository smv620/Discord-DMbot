"""Every setting the website's API reads reaches its container (#771 review): the
web-api service lists its settings by name (no env_file), so a new one is easy to miss."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "core" / "src" / "dmbot" / "web" / "settings.py"
COMPOSE = ROOT / "docker-compose.yml"
# Read by the API but only for running it locally, never in the container.
LOCAL_ONLY = {
    "WEB_DB_ANY_ROLE",  # any database user, for a developer's own machine
    "WEB_API_HOST",  # the container listens on its default
}


class _Recording(dict[str, str]):
    """An environment that remembers every name asked for, however it's read."""

    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(values)
        self.asked: set[str] = set()

    def get(self, key: str, default: Any = None) -> Any:
        self.asked.add(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.asked.add(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self.asked.add(key)
        return super().__contains__(key)


BASE = {
    "DATABASE_URL": "postgresql://x",
    "DISCORD_CLIENT_ID": "1",
    "DISCORD_CLIENT_SECRET": "s",
    "WEB_SECRET_KEY": "k" * 40,
    "WEB_SITE_URL": "https://dmbot.example/",
    "WEB_API_URL": "https://api.dmbot.example",
}


def read_names() -> set[str]:
    """Every name load_web_settings reads: asked for while loading (exact, whatever way
    it's read), plus any name in the file's text (names read only on some branches)."""
    from dmbot.web.settings import load_web_settings

    env = _Recording(BASE)
    load_web_settings(env)
    in_text = set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', SETTINGS.read_text("utf-8")))
    return env.asked | (in_text & _env_like(in_text))


def _env_like(names: set[str]) -> set[str]:
    """Names in the text that are settings, not other constants (they're all read with
    get() or listed as required in settings.py)."""
    text = SETTINGS.read_text("utf-8")
    return {n for n in names if re.search(rf'(get\(|REQUIRED|\(|, )\s*"{n}"', text)}


def web_api_names() -> set[str]:
    text = COMPOSE.read_text("utf-8")
    service = re.search(r"^  web-api:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", text, re.M | re.S)
    assert service is not None, "no web-api service in docker-compose.yml"
    block = re.search(r"^    environment:\n((?:^      .*\n)+)", service.group(1), re.M)
    assert block is not None, "web-api has no environment list"
    return set(re.findall(r"^      ([A-Z0-9_]+):", block.group(1), re.M))


class WebApiSettings(unittest.TestCase):
    def test_every_setting_the_api_reads_is_passed_to_its_container(self) -> None:
        missing = read_names() - LOCAL_ONLY - web_api_names()
        self.assertEqual(missing, set(), "add these to web-api's environment in docker-compose.yml")

    def test_the_scan_finds_names(self) -> None:
        self.assertIn("DMBOT_FREE_USERS", read_names())
        self.assertIn("DATABASE_URL", web_api_names())


if __name__ == "__main__":
    unittest.main()
