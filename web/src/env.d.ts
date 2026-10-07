interface ImportMetaEnv {
  /** The web API's address (core/src/dmbot/web/), or "mock" for the pretend API. */
  readonly PUBLIC_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
