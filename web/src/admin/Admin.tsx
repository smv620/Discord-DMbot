/** @jsxImportSource preact */
// The admin page (#772): the sign-in screen, then the signed-in area that part 3 (#773)
// fills with the free access list.
import { useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/admin";
import { type AdminApi, AdminApiError, type AdminMe, type AdminWays } from "./api";
import Grants from "./Grants";

interface Props {
  api: AdminApi;
  /** The page's query string: Google's sign-in comes back with ?signin=failed. */
  search: string;
}

type View =
  | { kind: "loading" }
  | { kind: "down" }
  | { kind: "off" }
  | { kind: "out"; ways: AdminWays }
  | { kind: "in"; me: AdminMe };

const signInNotices: Record<string, string> = {
  failed: text.googleFailed,
  off: text.googleOff,
};

function noticeFor(search: string): string | null {
  const why = new URLSearchParams(search).get("signin") ?? "";
  // Own keys only: ?signin=toString mustn't find the object's built-in function.
  return Object.hasOwn(signInNotices, why) ? (signInNotices[why] ?? null) : null;
}

/** The page's heading (in admin.astro): where focus goes once the view changes. */
function focusHeading() {
  document.getElementById("admin-heading")?.focus();
}

export default function Admin({ api, search }: Props) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(noticeFor(search));
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // 16 characters or more typed blind on a phone, with 5 tries: let the owner check them.
  const [showPassword, setShowPassword] = useState(false);
  const [refused, setRefused] = useState(0);
  const passwordBox = useRef<HTMLInputElement>(null);

  async function load(): Promise<void> {
    setView({ kind: "loading" });
    try {
      const me = await api.me();
      setView(me ? { kind: "in", me } : { kind: "out", ways: await api.ways() });
    } catch (error) {
      setView({ kind: error instanceof AdminApiError && error.kind === "off" ? "off" : "down" });
    }
  }

  useEffect(() => {
    void load();
    // Read once: a reload (or Back) after a later success mustn't show the old failure.
    if (typeof window !== "undefined" && window.location.search.includes("signin=")) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [api]);

  // After a refusal, straight back to the password box to try again.
  useEffect(() => {
    if (refused) passwordBox.current?.focus();
  }, [refused]);

  async function signIn(event: Event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.signIn(email.trim(), password);
      setPassword("");
      setShowPassword(false);
      await load();
      focusHeading();
    } catch (error) {
      setPassword("");
      const kind = error instanceof AdminApiError ? error.kind : "down";
      const google = view.kind === "out" && view.ways.google;
      const wrong = google ? text.wrong : text.wrongNoGoogle;
      setNotice(kind === "wrong" ? wrong : kind === "busy" ? text.busy : text.down);
      setRefused((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  async function signOut(csrf: string) {
    setBusy(true);
    try {
      await api.signOut(csrf);
      setNotice(text.signedOut);
      await load();
      focusHeading();
    } catch {
      setNotice(text.signOutFailed);
    } finally {
      setBusy(false);
    }
  }

  if (view.kind === "loading") return <p role="status">{text.loading}</p>;
  if (view.kind === "off") {
    return (
      <p class="warn" role="alert">
        {text.off}
      </p>
    );
  }
  if (view.kind === "down") {
    return (
      <div class="stack">
        {/* Kept: "You're signed out" stays true even if the page can't load after it. */}
        {notice && <p class={notice === text.signedOut ? "ok" : "warn"}>{notice}</p>}
        <p class="warn" role="alert">
          {text.down}
        </p>
        <button type="button" class="button" onClick={() => void load()}>
          {text.tryAgain}
        </button>
      </div>
    );
  }
  if (view.kind === "in") {
    return (
      <div class="stack">
        <p>{text.signedInAs(view.me.email)}</p>
        {notice && (
          <p class="warn" role="alert">
            {notice}
          </p>
        )}
        <button
          type="button"
          class="button secondary"
          disabled={busy}
          onClick={() => void signOut(view.me.csrf)}
        >
          {text.signOut}
        </button>
        <Grants
          api={api}
          csrf={view.me.csrf}
          onSignedOut={() => {
            setNotice(text.timedOut);
            void load();
          }}
          onOff={() => void load()}
        />
      </div>
    );
  }
  const { ways } = view;
  // Google's refusal can't point at a password form that isn't there.
  const shown =
    notice === text.googleFailed && !ways.password ? text.googleFailedNoPassword : notice;
  return (
    <div class="stack">
      <p>{text.signInLead}</p>
      {shown && (
        <p class={shown === text.signedOut ? "ok" : "warn"} role="alert">
          {shown}
        </p>
      )}
      {/* Only the sign-ins the server has set up: a button that can't work is a dead end. */}
      {ways.google && (
        <a class="button" href={api.googleUrl()}>
          {text.google}
        </a>
      )}
      {ways.password && (
        <form onSubmit={signIn} class="stack">
          {ways.google && <p>{text.or}</p>}
          <fieldset disabled={busy}>
            <label for="admin-email">{text.email}</label>
            <input
              id="admin-email"
              type="email"
              autocomplete="username"
              required
              value={email}
              onInput={(e) => setEmail(e.currentTarget.value)}
            />
            <label for="admin-password">{text.password}</label>
            <input
              id="admin-password"
              ref={passwordBox}
              type={showPassword ? "text" : "password"}
              autocomplete="current-password"
              required
              value={password}
              onInput={(e) => setPassword(e.currentTarget.value)}
            />
            <button
              type="button"
              class="button secondary"
              aria-controls="admin-password"
              onClick={() => setShowPassword((shown) => !shown)}
            >
              {showPassword ? text.hidePassword : text.showPassword}
            </button>
          </fieldset>
          <button type="submit" class="button" disabled={busy}>
            {busy ? text.signingIn : text.signIn}
          </button>
        </form>
      )}
    </div>
  );
}
