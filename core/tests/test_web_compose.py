"""Every setting the website's API reads reaches its container (#771 review): the
web-api service lists its settings by name (no env_file), so a new one is easy to miss."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "core" / "src" / "dmbot" / "web" / "settings.py"
COMPOSE = ROOT / "docker-compose.yml"
# Read by the API but only for running it locally, never in the container.
LOCAL_ONLY = {
    "WEB_DB_ANY_ROLE",  # any database user, for a developer's own machine
    "WEB_API_HOST",  # the container listens on its default
}


def read_names() -> set[str]:
    return set(re.findall(r'get\("([A-Z0-9_]+)"\)', SETTINGS.read_text("utf-8")))


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
