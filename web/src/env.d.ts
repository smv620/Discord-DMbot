interface ImportMetaEnv {
  /** The web API's address (core/src/dmbot/web/), or "mock" for the pretend API. */
  readonly PUBLIC_API_BASE?: string;
  /** Cloudflare Turnstile's site key for the "Say hello" forms (#665); empty to skip it. */
  readonly PUBLIC_TURNSTILE_SITE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
