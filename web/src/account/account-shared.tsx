/** @jsxImportSource preact */
// What My Account's sections share (#2743): the api and helpers every section reaches for, in a file of its
// own so a section that loads on demand (AccountEmail, AccountTalk) can use them without importing the page.
import type { ComponentChildren } from "preact";
import { createContext } from "preact";
import { useCallback, useContext, useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/account";
import { ApiError, type AccountApi } from "./api";

export interface Shared {
  api: AccountApi;
  /** Send the browser to Discord or the payment company; anything else is refused. */
  go: (url: string) => void;
  /** Fetch the account again after a change. A failure keeps what's on screen. */
  refresh: () => Promise<void>;
  /** The session ended (or must be renewed): back to sign-in, with a message. */
  signedOut: (notice?: string) => void;
  /** Free access with no hours or campaign limit: no "your hours" or "slots" wording (#824). */
  noLimits: boolean;
  /** Cloudflare Turnstile's site key for the Support form, or "" (#1340). */
  siteKey: string;
}

export const SharedContext = createContext<Shared | null>(null);

export function useShared(): Shared {
  const shared = useContext(SharedContext);
  if (!shared) throw new Error("Account sections need the Account component around them");
  return shared;
}

/**
 * One action at a time per section: the button shows "One moment…" while it runs, a second
 * tap does nothing, and a failure shows its message inside that section, where the user is.
 */
export function useAction() {
  const { signedOut } = useShared();
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (action: () => Promise<void>, explain?: (error: ApiError) => string | null) => {
      if (running.current) return;
      running.current = true;
      setBusy(true);
      setNotice(null);
      try {
        await action();
      } catch (error) {
        const apiError = error instanceof ApiError ? error : new ApiError("server");
        if (apiError.kind === "signed-out") {
          signedOut();
          return;
        }
        if (apiError.kind === "sign-in-again") {
          signedOut(text.signInAgain);
          return;
        }
        setNotice(explain?.(apiError) ?? text.errors[apiError.kind] ?? text.actionFailed);
      } finally {
        running.current = false;
        setBusy(false);
      }
    },
    [signedOut],
  );

  const clear = useCallback(() => setNotice(null), []);
  return { busy, notice, run, clear };
}


export function Notice({ message }: { message: string | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [message]);
  if (!message) return null;
  return (
    <p ref={ref} class="warn" role="alert">
      {message}
    </p>
  );
}

export function ActionButton({
  busy,
  onClick,
  kind = "primary",
  label,
  children,
}: {
  busy: boolean;
  onClick: () => void;
  kind?: "primary" | "secondary" | "danger";
  /** A spoken name that starts with the visible words but says which thing, when there are several. */
  label?: string;
  children: ComponentChildren;
}) {
  const classes = kind === "primary" ? "button" : `button ${kind}`;
  return (
    <button type="button" class={classes} disabled={busy} aria-busy={busy} aria-label={busy ? undefined : label} onClick={onClick}>
      {busy ? text.busy : children}
    </button>
  );
}


/** Take the code out of the address bar, so a reload or a shared screen can't use it again. */
export function forgetEmailLink(): void {
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
}

/** The code in `#email-confirm=...` (a change of the email DMbot uses, #2349), or null. */
export function emailConfirmToken(hash: string): string | null {
  const found = /^#email-confirm=([\w-]{1,200})$/.exec(hash);
  return found ? (found[1] ?? null) : null;
}

/** A change-of-email code survives the Discord sign-in round trip in this tab's session storage, because
 * the address's `#...` does not (#2349). It is removed once the API has answered it. */
const CONFIRM_KEY = "dmbot:email-confirm";
export function rememberConfirm(token: string): void {
  try {
    sessionStorage.setItem(CONFIRM_KEY, token);
  } catch {
    // Storage may be blocked; the code is then still in the address until sign-in.
  }
}
export function recalledConfirm(): string | null {
  try {
    const found = sessionStorage.getItem(CONFIRM_KEY);
    return found && /^[\w-]{1,200}$/.test(found) ? found : null;
  } catch {
    return null;
  }
}
export function forgetConfirm(): void {
  try {
    sessionStorage.removeItem(CONFIRM_KEY);
  } catch {
    // Nothing to remove.
  }
}

