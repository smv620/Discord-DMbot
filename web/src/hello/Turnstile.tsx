/** @jsxImportSource preact */
// Cloudflare Turnstile, the "are you a person?" check (#665). Only loaded when the site has
// a Turnstile key (PUBLIC_TURNSTILE_SITE_KEY); without one (local testing) the forms work
// and the API skips the check. The CSP allows its script and frame only in builds with a
// key (scripts/csp-hashes.mjs).
import { useEffect, useRef } from "preact/hooks";

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render(element: HTMLElement, options: Record<string, unknown>): string;
  reset(widget: string): void;
  remove(widget: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let loading: Promise<TurnstileApi> | null = null;

function load(): Promise<TurnstileApi> {
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error()));
    script.onerror = () => {
      loading = null; // let the next form try again
      reject(new Error());
    };
    document.head.append(script);
  });
  return loading;
}

interface Props {
  siteKey: string;
  /** The check's token, or "" when it has run out. */
  onToken: (token: string) => void;
  /** Change this number to start a fresh check (each token works once). */
  round: number;
}

export default function Turnstile({ siteKey, onToken, round }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const tokenTo = useRef(onToken);
  tokenTo.current = onToken;

  useEffect(() => {
    let gone = false;
    void load()
      .then((api) => {
        if (gone || !box.current) return;
        widget.current = api.render(box.current, {
          sitekey: siteKey,
          // The normal check is 300 px wide, wider than a form on a 360 px phone: use
          // the small square one there, so the page never scrolls sideways.
          size: box.current.clientWidth < 300 ? "compact" : "normal",
          callback: (token: string) => tokenTo.current(token),
          "expired-callback": () => tokenTo.current(""),
          "error-callback": () => tokenTo.current(""),
        });
      })
      .catch(() => {
        // The check couldn't load: sending says "couldn't check that you're a person".
      });
    return () => {
      gone = true;
      if (widget.current) window.turnstile?.remove(widget.current);
      widget.current = null;
    };
  }, [siteKey]);

  useEffect(() => {
    if (round > 0 && widget.current) {
      tokenTo.current("");
      window.turnstile?.reset(widget.current);
    }
  }, [round]);

  return <div ref={box} class="turnstile" />;
}
