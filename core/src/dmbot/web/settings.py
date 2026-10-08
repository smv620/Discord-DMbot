"""Settings for the website's API (#435), from environment variables (.env)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dmbot.config import ConfigError
from dmbot.web.admin import decode_hash

REQUIRED = (
    "DATABASE_URL",
    "DISCORD_CLIENT_ID",
    "DISCORD_CLIENT_SECRET",
    "WEB_SECRET_KEY",
    "WEB_SITE_URL",
    "WEB_API_URL",
)


@dataclass(frozen=True, slots=True)
class WebSettings:
    database_url: str = field(repr=False)
    # The Discord application (Developer Portal → OAuth2). The secret never leaves here.
    discord_client_id: str
    discord_client_secret: str = field(repr=False)
    # Signs the short-lived sign-in check (OAuth `state`). Any long random string.
    secret_key: bytes = field(repr=False)
    # The website, e.g. https://dmbot.example: the only origin allowed to call the API,
    # and where people land after signing in.
    site_url: str = "http://localhost:4321"
    # This API's public address, e.g. https://api.dmbot.example or https://dmbot.example/api.
    # Must be on the same site as the website: the sign-in cookie is SameSite=Lax.
    api_url: str = "http://localhost:8080"
    # The payment company: "" (none yet: buying answers payments_off), or "fake" for local
    # testing only. Paddle or Lemon Squeezy are added once the owner chooses (#435).
    payment_provider: str = ""
    # Checks that payment events really come from the payment company.
    payment_webhook_secret: bytes = field(default=b"", repr=False)
    # Connect as any database user, not only dmbot_web: for testing on localhost only.
    any_db_role: bool = False
    # The "Say hello" forms (#665): a GitHub token that can only write Discussions in
    # feedback_repo. Empty: the forms answer feedback_off.
    feedback_token: str = field(default="", repr=False)
    feedback_repo: str = "smv620/Discord-DMbot"
    # Cloudflare Turnstile's secret key, the check that a person sent the form. Needed
    # whenever the forms are on, except when testing on localhost.
    turnstile_secret: str = field(default="", repr=False)
    # The request header holding the visitor's address when a proxy sits in front (for
    # Cloudflare: CF-Connecting-IP). Empty: the connection's own address. Only set it when
    # every request comes through that proxy, or anyone could pick their own address.
    client_ip_header: str = ""
    # The admin page (#772): the only addresses that may sign in, lower case. Empty: the
    # admin sign-in is off.
    admin_emails: tuple[str, ...] = ()
    # The admin password's argon2id hash, base64 (scripts/set-admin-password writes it).
    # Empty: only "Sign in with Google" works.
    admin_password_hash: str = field(default="", repr=False)
    # Google Cloud console -> APIs & Services -> Credentials -> OAuth client (web). Empty:
    # only the password works.
    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    host: str = "0.0.0.0"  # inside its container; nothing is published to the internet
    port: int = 8080
    session_days: int = 30

    @property
    def site_origin(self) -> str:
        parts = urlsplit(self.site_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def secure_cookies(self) -> bool:
        """Secure cookies need https; plain http is allowed only for local testing."""
        return urlsplit(self.api_url).scheme == "https"

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.api_url.rstrip('/')}/auth/discord/callback"

    @property
    def admin_google_redirect_uri(self) -> str:
        return f"{self.api_url.rstrip('/')}/admin/auth/google/callback"

    @property
    def install_redirect_uri(self) -> str:
        return f"{self.api_url.rstrip('/')}/install/callback"


def load_web_settings(env: Mapping[str, str] | None = None) -> WebSettings:
    env = os.environ if env is None else env

    def get(name: str) -> str:
        return env.get(name, "").strip()

    missing = [name for name in REQUIRED if not get(name)]
    if missing:
        raise ConfigError(
            f"Missing required settings: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in (section 'Website API')."
        )
    secret = get("WEB_SECRET_KEY")
    if len(secret) < 32:
        raise ConfigError(
            "WEB_SECRET_KEY must be at least 32 characters. Use a long random string."
        )
    for name in ("WEB_SITE_URL", "WEB_API_URL"):
        parts = urlsplit(get(name))
        local = parts.hostname in ("localhost", "127.0.0.1")
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ConfigError(f"{name} must start with https:// (http:// only for localhost).")
    try:
        port = int(get("WEB_API_PORT") or "8080")
        session_days = int(get("WEB_SESSION_DAYS") or "30")
    except ValueError as exc:
        raise ConfigError("WEB_API_PORT and WEB_SESSION_DAYS must be whole numbers.") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("WEB_API_PORT must be between 1 and 65535.")
    if not 1 <= session_days <= 90:
        raise ConfigError("WEB_SESSION_DAYS must be between 1 and 90.")
    provider = get("PAYMENT_PROVIDER").lower()
    webhook_secret = get("PAYMENT_WEBHOOK_SECRET")
    if provider not in ("", "fake"):
        raise ConfigError(
            f"PAYMENT_PROVIDER={provider} isn't built yet. Leave it empty for now (see #435)."
        )
    if provider == "fake" and urlsplit(get("WEB_API_URL")).hostname not in (
        "localhost",
        "127.0.0.1",
    ):
        raise ConfigError("PAYMENT_PROVIDER=fake is only for testing on localhost.")
    any_db_role = get("WEB_DB_ANY_ROLE") == "1"
    if any_db_role and urlsplit(get("WEB_API_URL")).hostname not in ("localhost", "127.0.0.1"):
        raise ConfigError("WEB_DB_ANY_ROLE=1 is only for testing on localhost.")
    if provider and len(webhook_secret) < 16:
        raise ConfigError("PAYMENT_WEBHOOK_SECRET must be set (16 characters or more).")
    feedback_token = get("GITHUB_FEEDBACK_TOKEN")
    feedback_repo = get("GITHUB_FEEDBACK_REPO") or "smv620/Discord-DMbot"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", feedback_repo):
        raise ConfigError("GITHUB_FEEDBACK_REPO must look like owner/name.")
    turnstile_secret = get("TURNSTILE_SECRET_KEY")
    if (
        feedback_token
        and not turnstile_secret
        and urlsplit(get("WEB_API_URL")).hostname not in ("localhost", "127.0.0.1")
    ):
        # Without it, a script could fill the repository with posts from many addresses.
        raise ConfigError("TURNSTILE_SECRET_KEY must be set when GITHUB_FEEDBACK_TOKEN is.")
    client_ip_header = get("WEB_CLIENT_IP_HEADER")
    if client_ip_header and not re.fullmatch(r"[A-Za-z0-9-]+", client_ip_header):
        raise ConfigError("WEB_CLIENT_IP_HEADER must be a header name, like CF-Connecting-IP.")
    if (
        feedback_token
        and not client_ip_header
        and urlsplit(get("WEB_API_URL")).hostname not in ("localhost", "127.0.0.1")
    ):
        # Behind the proxy every visitor has the proxy's address: without the header, one
        # message would use up the whole site's turn for 10 minutes.
        raise ConfigError("WEB_CLIENT_IP_HEADER must be set when GITHUB_FEEDBACK_TOKEN is.")
    admin_emails = tuple(
        sorted({e.strip().lower() for e in get("ADMIN_EMAILS").split(",") if e.strip()})
    )
    if any("@" not in e for e in admin_emails):
        raise ConfigError("ADMIN_EMAILS must be email addresses, separated by commas.")
    admin_password_hash = get("ADMIN_PASSWORD_HASH")
    if admin_password_hash and decode_hash(admin_password_hash) is None:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH isn't one scripts/set-admin-password made. Run it again."
        )
    google_client_id = get("GOOGLE_CLIENT_ID")
    google_client_secret = get("GOOGLE_CLIENT_SECRET")
    if bool(google_client_id) != bool(google_client_secret):
        raise ConfigError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET go together: set both.")
    return WebSettings(
        database_url=get("DATABASE_URL"),
        discord_client_id=get("DISCORD_CLIENT_ID"),
        discord_client_secret=get("DISCORD_CLIENT_SECRET"),
        secret_key=secret.encode("utf-8"),
        site_url=get("WEB_SITE_URL").rstrip("/"),
        api_url=get("WEB_API_URL").rstrip("/"),
        host=get("WEB_API_HOST") or "0.0.0.0",
        port=port,
        session_days=session_days,
        payment_provider=provider,
        payment_webhook_secret=webhook_secret.encode("utf-8"),
        any_db_role=any_db_role,
        feedback_token=feedback_token,
        feedback_repo=feedback_repo,
        turnstile_secret=turnstile_secret,
        client_ip_header=client_ip_header,
        admin_emails=admin_emails,
        admin_password_hash=admin_password_hash,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
    )
