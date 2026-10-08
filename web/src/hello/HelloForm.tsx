/** @jsxImportSource preact */
// One "Say hello" form (#665): a message box, an optional "how to reach you" box and one
// button. Used twice on /hello, for feedback and for questions.
import { useState } from "preact/hooks";

import { lettersLeft, MAX_MESSAGE, text } from "../content/hello";
import type { Kind, Send, SendResult } from "./api";
import Turnstile from "./Turnstile";

/** Counted like the API counts them: an emoji is one, not two. */
const letters = (s: string): number => Array.from(s).length;

interface Props {
  kind: Kind;
  send: Send;
  /** Cloudflare Turnstile's site key, or "" to skip the check (local testing). */
  siteKey: string;
}

export default function HelloForm({ kind, send, siteKey }: Props) {
  const words = text[kind];
  const [message, setMessage] = useState("");
  const [contact, setContact] = useState("");
  const [token, setToken] = useState("");
  const [round, setRound] = useState(0);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SendResult | null>(null);
  const id = `hello-${kind}`;

  async function submit(event: Event) {
    event.preventDefault();
    if (busy) return;
    // Checked here too, so the person hears it at once (the API checks again).
    const typed = message.trim();
    const local: SendResult | null =
      typed === "" ? "empty" : letters(typed) > MAX_MESSAGE ? "too-long" : null;
    if (local) {
      setResult(local);
      return;
    }
    setBusy(true);
    setResult(null);
    const outcome = await send({ kind, message: typed, contact, turnstile: token });
    setBusy(false);
    setResult(outcome);
    if (outcome === "sent") {
      setMessage("");
      setContact("");
    }
    // A Turnstile token works once: start a fresh check for the next try.
    if (siteKey) setRound((r) => r + 1);
  }

  if (result === "sent") {
    return (
      <section class="panel" aria-labelledby={`${id}-heading`}>
        <h2 id={`${id}-heading`}>{words.heading}</h2>
        <p class="ok" role="status">
          {text.sent}
        </p>
        <button type="button" class="link" onClick={() => setResult(null)}>
          {text.sendAnother}
        </button>
      </section>
    );
  }

  return (
    <section class="panel" aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>{words.heading}</h2>
      <form onSubmit={submit} noValidate>
        <label for={`${id}-message`}>{words.label}</label>
        <textarea
          id={`${id}-message`}
          rows={5}
          value={message}
          onInput={(e) => setMessage(e.currentTarget.value)}
          aria-describedby={`${id}-left`}
        />
        <p id={`${id}-left`} class="small muted" aria-live="polite">
          {lettersLeft(letters(message.trim()))}
        </p>
        <label for={`${id}-contact`}>{text.contactLabel}</label>
        <input
          id={`${id}-contact`}
          type="text"
          autocomplete="email"
          maxLength={200}
          value={contact}
          onInput={(e) => setContact(e.currentTarget.value)}
          aria-describedby={`${id}-hint`}
        />
        <p id={`${id}-hint`} class="small muted">
          {text.contactHint}
        </p>
        {siteKey && <Turnstile siteKey={siteKey} onToken={setToken} round={round} />}
        {result && (
          <p class="warn" role="alert">
            {text.errors[result]}
          </p>
        )}
        <button type="submit" class="button" disabled={busy}>
          {busy ? text.sending : words.send}
        </button>
      </form>
    </section>
  );
}
