/** @jsxImportSource preact */
// The one line at the top of every signed-in page (#2301): "Signed in as <name> · Not you? Sign out",
// or "You're not signed in." with the sign-in button. One component for all the pages, so the words
// and the behaviour cannot drift apart. It asks the API who is signed in itself, so a page does not
// have to pass anything down. The line keeps its height while it asks, so the page doesn't jump.
import { useEffect, useState } from "preact/hooks";

import { signedInLine as words } from "../content/signed-in-words";
import { SIGNED_OUT_EVENT, type AccountApi } from "./api";

type Who = { kind: "asking" } | { kind: "in"; name: string } | { kind: "out" } | { kind: "unknown" };

export default function SignedInLine({ api }: { api: AccountApi }) {
  const [who, setWho] = useState<Who>({ kind: "asking" });
  const [leaving, setLeaving] = useState(false);
  const [problem, setProblem] = useState(false);

  useEffect(() => {
    let current = true;
    api.me().then(
      (me) => current && setWho(me ? { kind: "in", name: me.user.name } : { kind: "out" }),
      // The page below says what went wrong; this line stays quiet rather than guess.
      () => current && setWho({ kind: "unknown" }),
    );
    return () => {
      current = false;
    };
  }, [api]);

  // The sign-in can end after the page opened (it expired, or was ended elsewhere): any call that
  // learns so tells this line, so it offers the one Sign in button.
  useEffect(() => {
    const ended = (): void => setWho({ kind: "out" });
    window.addEventListener(SIGNED_OUT_EVENT, ended);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, ended);
  }, []);

  const signOut = async (): Promise<void> => {
    setLeaving(true);
    setProblem(false);
    try {
      await api.signOut();
      // The page was built for someone signed in: start it again, so it shows the signed-out view.
      window.location.reload();
    } catch {
      setLeaving(false);
      setProblem(true);
    }
  };

  return (
    <div class="signed-in-line" aria-live="polite">
      {who.kind === "asking" && <span class="sr-only">{words.checking}</span>}
      {who.kind === "in" && (
        <p>
          {words.as} <strong>{who.name}</strong> <span aria-hidden="true">·</span> {words.notYou}{" "}
          <button type="button" class="link" disabled={leaving} onClick={() => void signOut()}>
            {leaving ? words.signingOut : words.signOut}
          </button>
        </p>
      )}
      {who.kind === "out" && (
        <p>
          {words.notSignedIn}{" "}
          <a class="button" href={api.signInUrl()}>
            {words.signIn}
          </a>
        </p>
      )}
      {problem && (
        <p class="note bad" role="alert">
          {words.couldNotSignOut}
        </p>
      )}
    </div>
  );
}
