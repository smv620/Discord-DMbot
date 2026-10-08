"""Every setting the website's API reads reaches its container (#771 review): the
web-api service lists its settings by name (no env_file), so a new one is easy to miss.
Also the Cloudflare Tunnel in front of it (#836): the tunnel is the only way in."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "core" / "src" / "dmbot" / "web" / "settings.py"
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
PORTS = re.compile(r"^    ports:", re.M)  # a published port, at a service's top level
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


def service_block(name: str) -> str:
    """The text of one service in docker-compose.yml."""
    text = COMPOSE.read_text("utf-8")
    service = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z]|\Z)", text, re.M | re.S)
    assert service is not None, f"no {name} service in docker-compose.yml"
    return service.group(1)


def web_api_names() -> set[str]:
    service = service_block("web-api")
    block = re.search(r"^    environment:\n((?:^      .*\n)+)", service, re.M)
    assert block is not None, "web-api has no environment list"
    return set(re.findall(r"^      ([A-Z0-9_]+):", block.group(1), re.M))


class WebApiSettings(unittest.TestCase):
    def test_every_setting_the_api_reads_is_passed_to_its_container(self) -> None:
        missing = read_names() - LOCAL_ONLY - web_api_names()
        self.assertEqual(missing, set(), "add these to web-api's environment in docker-compose.yml")

    def test_the_scan_finds_names(self) -> None:
        self.assertIn("DMBOT_FREE_USERS", read_names())
        self.assertIn("DATABASE_URL", web_api_names())


class CloudflareTunnel(unittest.TestCase):
    """The only way in to web-api is the tunnel, which only calls out (#836)."""

    def test_web_api_publishes_no_port(self) -> None:
        # `expose` keeps it on the private network; `ports` would open it to the world.
        self.assertNotRegex(service_block("web-api"), PORTS, "web-api must not publish a port")

    def test_the_tunnel_publishes_no_port(self) -> None:
        self.assertNotRegex(
            service_block("cloudflared"), r"^    ports:", "the tunnel only calls out"
        )

    def test_the_tunnel_image_is_pinned(self) -> None:
        image = re.search(r"^    image: (\S+)", service_block("cloudflared"), re.M)
        assert image is not None, "cloudflared has no image"
        name, _, tag = image.group(1).partition(":")
        self.assertEqual(name, "cloudflare/cloudflared")
        self.assertRegex(tag, r"^\d+\.\d+\.\d+$", "pin a version, not latest")

    def test_the_tunnel_starts_with_web_api_and_gets_only_its_token(self) -> None:
        service = service_block("cloudflared")
        self.assertIn('profiles: ["web"]', service)
        self.assertRegex(service, r"depends_on:\n\s+- web-api")
        self.assertRegex(service, r"restart: unless-stopped")
        self.assertRegex(service, r"TUNNEL_TOKEN: \$\{CLOUDFLARE_TUNNEL_TOKEN:-\}")
        self.assertNotIn("env_file", service, "list the settings by name")

    def test_the_client_address_header_is_still_passed_to_web_api(self) -> None:
        # Only the tunnel reaches web-api, so CF-Connecting-IP can be trusted there.
        self.assertIn("WEB_CLIENT_IP_HEADER", web_api_names())

    def test_the_token_is_in_the_env_example_and_set_key_accepts_it(self) -> None:
        names = re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text("utf-8"), re.M)
        self.assertIn("CLOUDFLARE_TUNNEL_TOKEN", names)
        self.assertTrue("CLOUDFLARE_TUNNEL_TOKEN".endswith("_TOKEN"))  # scripts/set-key's rule


if __name__ == "__main__":
    unittest.main()
