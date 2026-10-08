/** @jsxImportSource preact */
// The admin page (#772): the sign-in screen, then the signed-in area that part 3 (#773)
// fills with the free access list.
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/admin";
import { type AdminApi, AdminApiError, type AdminMe } from "./api";

interface Props {
  api: AdminApi;
  /** The page's query string: Google's sign-in comes back with ?signin=failed. */
  search: string;
}

type View =
  | { kind: "loading" }
  | { kind: "down" }
  | { kind: "off" }
  | { kind: "out" }
  | { kind: "in"; me: AdminMe };

const signInNotices: Record<string, string> = {
  failed: text.googleFailed,
  off: text.googleOff,
};

export default function Admin({ api, search }: Props) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(
    signInNotices[new URLSearchParams(search).get("signin") ?? ""] ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function load() {
    setView({ kind: "loading" });
    try {
      const me = await api.me();
      setView(me ? { kind: "in", me } : { kind: "out" });
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

  async function signIn(event: Event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.signIn(email.trim(), password);
      setPassword("");
      await load();
    } catch (error) {
      setPassword("");
      setNotice(error instanceof AdminApiError && error.kind === "wrong" ? text.wrong : text.down);
    } finally {
      setBusy(false);
    }
  }

  async function signOut(csrf: string) {
    setBusy(true);
    try {
      await api.signOut(csrf);
      setNotice(text.signedOut);
      setView({ kind: "out" });
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
        <p class="muted">{text.comingSoon}</p>
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
      </div>
    );
  }
  return (
    <div class="stack">
      <p>{text.signInLead}</p>
      {notice && (
        <p class={notice === text.signedOut ? "ok" : "warn"} role="alert">
          {notice}
        </p>
      )}
      <a class="button" href={api.googleUrl()}>
        {text.google}
      </a>
      <form onSubmit={signIn} class="stack">
        <p>{text.or}</p>
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
            type="password"
            autocomplete="current-password"
            required
            value={password}
            onInput={(e) => setPassword(e.currentTarget.value)}
          />
        </fieldset>
        <button type="submit" class="button" disabled={busy}>
          {busy ? text.signingIn : text.signIn}
        </button>
      </form>
    </div>
  );
}
