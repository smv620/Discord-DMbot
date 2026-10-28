"""Settings for the website's API (#435), from environment variables (.env)."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dmbot import imports_switch
from dmbot.config import MODEL_NAME, ConfigError
from dmbot.entitlements import parse_free_users
from dmbot.mail import MailConfigError, MailSettings, load_mail_settings, one_address
from dmbot.transcription.config import OUTSIDE_ENGINES
from dmbot.web.admin_hash import decode_hash
from dmbot.web.payments import paid_plan_ids
from dmbot.web.stripe import StripeConfig
from dmbot.web.support_mail import reply_domain

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
    # testing only. Stripe is the real one (owner, 2026-10-13, #1110).
    payment_provider: str = ""
    # Checks that payment events really come from the payment company.
    payment_webhook_secret: bytes = field(default=b"", repr=False)
    # Stripe's key and ids, when payment_provider is "stripe" (#1110).
    stripe: StripeConfig | None = field(default=None, repr=False)
    # Connect as any database user, not only dmbot_web: for testing on localhost only.
    any_db_role: bool = False
    # The "Say hello" forms (#665): a GitHub token that can only write Discussions in
    # feedback_repo. Empty: the forms answer feedback_off.
    feedback_token: str = field(default="", repr=False)
    # Signs personal links (dmbot.link_tokens, #1228). Empty: personal links are off.
    link_token_secret: bytes = field(default=b"", repr=False)
    # Account emails, such as sign-in links (#1231). Nothing is sent until SMTP is set up.
    mail: MailSettings = field(default_factory=MailSettings)
    feedback_repo: str = "smv620/Discord-DMbot"
    # The monthly recap allowances (#1428), the same two settings the bot reads, so the page
    # can say "N of M recaps this month".
    recaps_per_month: int = 10
    recap_audio_per_month: int = 8
    # Where "a support ticket is waiting" goes (#1340). Empty: tickets are still kept and shown on
    # the admin page, and nobody is emailed. The email never holds the ticket's words.
    support_team_email: str = ""
    # The support helper (#1431): the AI account it answers with (the same key the bot uses), the
    # strong tier's model for it (empty: the tier's default), and the secret that proves an
    # inbound email really came from the mail service (empty: replies by email are off).
    ai_key: str = field(default="", repr=False)
    support_ai_model: str = ""
    mail_inbound_secret: str = field(default="", repr=False)
    # Search by meaning on the Names page (#2733): the same switch, key and models the bot reads
    # (#2664), so the search words are turned into numbers with the model the kept numbers were
    # made with. Off until the switch is 1 and a key is set.
    meaning_search: bool = False
    voyage_key: str = field(default="", repr=False)
    voyage_embed_model: str = "voyage-3.5"
    voyage_rerank_model: str = "rerank-2.5"
    # Where people's emailed replies to a support ticket go (#1717): a subdomain only the mail
    # service receives. Empty: the support sender's own domain.
    mail_reply_domain: str = ""
    # Where the bot keeps recap audio on disk, read here only (the volume is mounted read-only).
    # Empty: the page shows no players and the file route answers not_found (#1418).
    recap_audio_dir: str = ""
    # The outside speech-to-text company the bot sends voices to (deepgram, cloud), or None: only
    # consent given for it counts, as in the bot. From TRANSCRIBER, the bot's own setting.
    outside_engine: str | None = None
    # Cloudflare Turnstile's secret key, the check that a person sent the form. Needed
    # whenever the forms are on, except when testing on localhost.
    turnstile_secret: str = field(default="", repr=False)
    # The request header holding the visitor's address when a proxy sits in front (for
    # Cloudflare: CF-Connecting-IP). Empty: the connection's own address. Only set it when
    # every request comes through that proxy, or anyone could pick their own address.
    client_ip_header: str = ""
    # The admin page (#772): the only addresses that may sign in, lower case. Empty: the
    # admin sign-in is off.
    # repr=False: the admin's address stays out of any log that prints the settings.
    admin_emails: tuple[str, ...] = field(default=(), repr=False)
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
    # The owner's own Discord accounts: free access, no caps (#771). Never logged.
    free_users: frozenset[int] = field(default=frozenset(), repr=False)
    # The same switch as the bot's: when on, the site's hand-over accept checks the campaign
    # cap too, so the two never disagree (#437 part 2c).
    enforce_plans: bool = False
    # Importing older sessions (#2105): the routes answer 404 until dev1 has checked a real
    # import (DMBOT_IMPORTS=1). Off by default so the API is not live before that.
    imports_on: bool = False
    # The same DMBOT_HANDOVER_EMAIL as the bot's (#2532): off, the email hand-over routes say 404.
    handover_email_on: bool = False
    # A person's own "Delete my transcripts" on the website (#2769; DMBOT_WEB_LINE_DELETE): off, its
    # routes answer 404, so nothing is queued before the bot can run it.
    line_delete_on: bool = False
    # Game masters added and removed on the website (#2768; DMBOT_WEB_CO_DMS): off, those routes and
    # the queue's `co_dm` choice answer 404, so nothing is queued before the bot can apply it.
    co_dms_on: bool = False
    # "Make it again" on a session's report (#2661; DMBOT_WEB_REPORT_MAKE): off, the route that
    # queues the request answers 404, so nothing is queued before the bot (#2805) takes it.
    report_make_on: bool = False
    # Sharing house rules from the website (#2783; DMBOT_WEB_RULE_SHARING): off, the routes that
    # queue a choice answer 404, so nothing is queued before the bot can run it.
    rule_sharing_on: bool = False
    # Servers where it is on although DMBOT_IMPORTS is off (#2259): the owner's test servers. Never
    # logged or shown.
    imports_servers: frozenset[int] = field(default=frozenset(), repr=False)
    # The same DMBOT_RECAPS as the bot's: off, nothing makes a recap's narration, so "make it
    # again" is refused instead of promising it (#2183).
    recaps_on: bool = False
    # Whether the bot can make narration at all: DMBOT_RECAPS is on and the bot has a voice (the
    # same rule as `Settings.recap_audio_on`). The web container is never given the speech key, so
    # compose passes only whether each is set ("1" or empty), never the key itself (#2419).
    recap_audio_on: bool = False
    # PlotBot's check of each recap (#2122): the same DMBOT_PLOT_CHECK as the bot's, and only with
    # recaps on. Off, the plot routes say "unavailable" and Approve waits for nothing.
    plot_check_on: bool = False
    # The bot's DMBOT_RECAP_VOICE_TAGS, to work out which paragraphs a change to the words would
    # speak again (#2630): the tags are part of what the voice is sent. It changes nothing the
    # bot does.
    recap_voice_tags: bool = True

    def imports_enabled_for(self, guild_id: int) -> bool:
        """Whether importing is on for this server (#2259)."""
        return imports_switch.enabled(self.imports_on, self.imports_servers, guild_id)

    @property
    def meaning_search_on(self) -> bool:
        return self.meaning_search and bool(self.voyage_key)

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

    monthly: dict[str, int] = {}
    for name, default in (("DMBOT_RECAPS_PER_MONTH", "10"), ("DMBOT_RECAP_AUDIO_PER_MONTH", "8")):
        raw = get(name) or default
        if not (raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= 1000:
            raise ConfigError(f'{name} must be a whole number from 1 to 1000, got "{raw}".')
        monthly[name] = int(raw)

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
    if provider not in ("", "fake", "stripe"):
        raise ConfigError(
            f"PAYMENT_PROVIDER={provider} isn't built. Use stripe, or leave it empty."
        )
    stripe = _stripe(get) if provider == "stripe" else None
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
    try:
        mail = load_mail_settings(env)
    except MailConfigError as exc:
        raise ConfigError(str(exc)) from exc
    link_secret = get("LINK_TOKEN_SECRET")
    if link_secret and len(link_secret) < 32:
        raise ConfigError("LINK_TOKEN_SECRET must be at least 32 characters (or empty).")
    feedback_repo = get("GITHUB_FEEDBACK_REPO") or "smv620/Discord-DMbot"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", feedback_repo):
        raise ConfigError("GITHUB_FEEDBACK_REPO must look like owner/name.")
    meaning_raw = (get("DMBOT_MEANING_SEARCH") or "0").lower()
    if meaning_raw not in ("0", "1", "true", "on", "false", "off"):
        raise ConfigError(f'DMBOT_MEANING_SEARCH must be 1 (on) or 0 (off), got "{meaning_raw}".')
    voyage_models = []
    for model_name, default in (
        ("VOYAGE_EMBED_MODEL", "voyage-3.5"),
        ("VOYAGE_RERANK_MODEL", "rerank-2.5"),
    ):
        model = get(model_name) or default
        if not (
            model.isascii() and model.replace("-", "").replace(".", "").replace("_", "").isalnum()
        ):
            raise ConfigError(f"{model_name} must be a model name such as {default}.")
        voyage_models.append(model)
    support_team_email = get("SUPPORT_TEAM_EMAIL")
    if support_team_email and not one_address(support_team_email):
        raise ConfigError("SUPPORT_TEAM_EMAIL must be one email address, like team@example.com.")
    support_ai_model = get("SUPPORT_AI_MODEL").strip("\"' ")
    if support_ai_model and not MODEL_NAME.match(support_ai_model):
        raise ConfigError(
            "SUPPORT_AI_MODEL isn't a Claude model name. Use the name as Anthropic lists it "
            "(no quotes or spaces), or leave it empty to use the strong tier's default."
        )
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
        raise ConfigError(
            "ADMIN_EMAILS must be email addresses, separated by commas, like a@x.com,b@y.com."
        )
    admin_password_hash = get("ADMIN_PASSWORD_HASH")
    if admin_password_hash and decode_hash(admin_password_hash) is None:
        raise ConfigError("ADMIN_PASSWORD_HASH isn't valid. Run scripts/set-admin-password again.")
    if (
        admin_emails
        and not client_ip_header
        and urlsplit(get("WEB_API_URL")).hostname not in ("localhost", "127.0.0.1")
    ):
        # Behind the proxy every visitor has the proxy's address: five wrong tries by
        # anyone would lock the admin out, by password and by Google.
        raise ConfigError(
            "WEB_CLIENT_IP_HEADER must be set when ADMIN_EMAILS is. Behind Cloudflare, set it"
            " to CF-Connecting-IP."
        )
    google_client_id = get("GOOGLE_CLIENT_ID")
    google_client_secret = get("GOOGLE_CLIENT_SECRET")
    if bool(google_client_id) != bool(google_client_secret):
        raise ConfigError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET go together: set both.")
    if admin_emails and not admin_password_hash and not google_client_id:
        # Otherwise the page would show a form that refuses every try.
        raise ConfigError(
            "ADMIN_EMAILS is set but there's no way to sign in. Run scripts/set-admin-password,"
            " or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or empty ADMIN_EMAILS to keep"
            " the admin page off."
        )
    try:
        free_users = parse_free_users(get("DMBOT_FREE_USERS"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    enforce_raw = (get("DMBOT_ENFORCE_PLANS") or "0").lower()
    if enforce_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(f'DMBOT_ENFORCE_PLANS must be 1 (on) or 0 (off), got "{enforce_raw}".')
    imports_raw = (get("DMBOT_IMPORTS") or "0").lower()
    if imports_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(f'DMBOT_IMPORTS must be 1 (on) or 0 (off), got "{imports_raw}".')
    try:
        imports_servers = imports_switch.parse_servers(get("DMBOT_IMPORTS_SERVERS"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    recaps_raw = (get("DMBOT_RECAPS") or "0").lower()
    if recaps_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(f'DMBOT_RECAPS must be 1 (on) or 0 (off), got "{recaps_raw}".')
    handover_email_raw = (get("DMBOT_HANDOVER_EMAIL") or "0").lower()
    if handover_email_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            f'DMBOT_HANDOVER_EMAIL must be 1 (on) or 0 (off), got "{handover_email_raw}".'
        )
    line_delete_raw = (get("DMBOT_WEB_LINE_DELETE") or "0").lower()
    if line_delete_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            f'DMBOT_WEB_LINE_DELETE must be 1 (on) or 0 (off), got "{line_delete_raw}".'
        )
    co_dms_raw = (get("DMBOT_WEB_CO_DMS") or "0").lower()
    if co_dms_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(f'DMBOT_WEB_CO_DMS must be 1 (on) or 0 (off), got "{co_dms_raw}".')
    report_make_raw = (get("DMBOT_WEB_REPORT_MAKE") or "0").lower()
    if report_make_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            f'DMBOT_WEB_REPORT_MAKE must be 1 (on) or 0 (off), got "{report_make_raw}".'
        )
    rule_sharing_raw = (get("DMBOT_WEB_RULE_SHARING") or "0").lower()
    if rule_sharing_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            f'DMBOT_WEB_RULE_SHARING must be 1 (on) or 0 (off), got "{rule_sharing_raw}".'
        )
    return WebSettings(
        database_url=get("DATABASE_URL"),
        rule_sharing_on=rule_sharing_raw in ("1", "true", "on"),
        handover_email_on=handover_email_raw in ("1", "true", "on"),
        line_delete_on=line_delete_raw in ("1", "true", "on"),
        co_dms_on=co_dms_raw in ("1", "true", "on"),
        report_make_on=report_make_raw in ("1", "true", "on"),
        recaps_on=recaps_raw in ("1", "true", "on"),
        plot_check_on=recaps_raw in ("1", "true", "on")
        and (get("DMBOT_PLOT_CHECK") or "0").lower() in ("1", "true", "on"),
        recap_audio_on=(
            recaps_raw in ("1", "true", "on")
            and get("RECAP_AUDIO_KEY_SET") == "1"
            and get("RECAP_AUDIO_VOICE_SET") == "1"
        ),
        recap_voice_tags=(get("DMBOT_RECAP_VOICE_TAGS") or "1").lower() in ("1", "true", "on"),
        enforce_plans=enforce_raw in ("1", "true", "on"),
        imports_on=imports_raw in ("1", "true", "on"),
        imports_servers=imports_servers,
        discord_client_id=get("DISCORD_CLIENT_ID"),
        discord_client_secret=get("DISCORD_CLIENT_SECRET"),
        secret_key=secret.encode("utf-8"),
        site_url=get("WEB_SITE_URL").rstrip("/"),
        api_url=get("WEB_API_URL").rstrip("/"),
        host=get("WEB_API_HOST") or "0.0.0.0",
        port=port,
        session_days=session_days,
        free_users=free_users,
        payment_provider=provider,
        payment_webhook_secret=webhook_secret.encode("utf-8"),
        stripe=stripe,
        any_db_role=any_db_role,
        feedback_token=feedback_token,
        link_token_secret=link_secret.encode("utf-8"),
        mail=mail,
        feedback_repo=feedback_repo,
        recaps_per_month=monthly["DMBOT_RECAPS_PER_MONTH"],
        recap_audio_per_month=monthly["DMBOT_RECAP_AUDIO_PER_MONTH"],
        support_team_email=support_team_email,
        ai_key=get("ANTHROPIC_API_KEY"),
        support_ai_model=support_ai_model,
        mail_inbound_secret=get("MAIL_INBOUND_SECRET"),
        meaning_search=meaning_raw in ("1", "true", "on"),
        voyage_key=get("VOYAGE_API_KEY"),
        voyage_embed_model=voyage_models[0],
        voyage_rerank_model=voyage_models[1],
        mail_reply_domain=reply_domain(get("MAIL_REPLY_DOMAIN")),
        recap_audio_dir=get("WEB_RECAP_AUDIO_DIR"),
        outside_engine=get("TRANSCRIBER").lower()
        if get("TRANSCRIBER").lower() in OUTSIDE_ENGINES
        else None,
        turnstile_secret=turnstile_secret,
        client_ip_header=client_ip_header,
        admin_emails=admin_emails,
        admin_password_hash=admin_password_hash,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
    )


def _stripe(get: Callable[[str], str]) -> StripeConfig:
    """Stripe's settings (#1110). Every buyable plan needs its price, so a missing one stops
    the API starting instead of failing at someone's checkout."""
    key = get("STRIPE_SECRET_KEY")
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        raise ConfigError(
            "STRIPE_SECRET_KEY must be set to a Stripe secret key (sk_test_... or sk_live_...)."
        )
    prices: dict[str, str] = {}
    for plan_id in paid_plan_ids():
        suffix = plan_id.upper().replace("-", "_")
        price = get(f"STRIPE_PRICE_{suffix}")
        if not price.startswith("price_") or not price.isascii():
            raise ConfigError(
                f"STRIPE_PRICE_{suffix} must be the plan's Stripe price id (price_...)."
            )
        prices[plan_id] = price
    extra = get("STRIPE_PRICE_EXTRA_HOURS")
    if not extra.startswith("price_") or not extra.isascii():
        raise ConfigError("STRIPE_PRICE_EXTRA_HOURS must be the extra hours' Stripe price id.")
    # The recap audio add-on's price (#1389) is optional: the owner sets it later (#1385), and
    # until then the add-on is not for sale and its checkout is hidden.
    recap_audio = get("STRIPE_PRICE_RECAP_AUDIO")
    if recap_audio and (not recap_audio.startswith("price_") or not recap_audio.isascii()):
        raise ConfigError(
            "STRIPE_PRICE_RECAP_AUDIO must be the add-on's Stripe price id (price_...)."
        )
    ids = [*prices.values(), extra, *([recap_audio] if recap_audio else [])]
    if len(set(ids)) != len(ids):
        raise ConfigError("Each STRIPE_PRICE_* must be a different price.")
    managed = (get("STRIPE_MANAGED_PAYMENTS") or "0").lower()
    if managed not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(f'STRIPE_MANAGED_PAYMENTS must be 1 or 0, got "{managed}".')
    return StripeConfig(
        secret_key=key,
        managed_payments=managed in ("1", "true", "on"),
        prices=prices,
        extra_hours_price=extra,
        recap_audio_price=recap_audio,
    )
