interface ImportMetaEnv {
  /** The web API's address (core/src/dmbot/web/), or "mock" for the pretend API. */
  readonly PUBLIC_API_BASE?: string;
  /** Cloudflare Turnstile's site key for the "Say hello" forms (#665); empty to skip it. */
  readonly PUBLIC_TURNSTILE_SITE_KEY?: string;
  /** Stripe's billing login link (#1711), test or live; only a https://billing.stripe.com/ address is shown. */
  readonly PUBLIC_STRIPE_PORTAL_LOGIN_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
