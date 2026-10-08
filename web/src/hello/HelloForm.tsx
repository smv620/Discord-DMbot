/** @jsxImportSource preact */
// One "Say hello" form (#665): a message box, an optional "how to reach you" box and one
// button. Used twice on /hello, for feedback and for questions.
import { useEffect, useRef, useState } from "preact/hooks";

import { COUNT_FROM, lettersLeft, MAX_CONTACT, MAX_MESSAGE, text } from "../content/hello";
import type { Kind, Refusal, Send } from "./api";
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
  // The person check starts when someone begins to use this form, not for every reader.
  const [started, setStarted] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [posted, setPosted] = useState<string | null>(null);
  const messageBox = useRef<HTMLTextAreaElement>(null);
  const contactBox = useRef<HTMLInputElement>(null);
  const thanks = useRef<HTMLParagraphElement>(null);
  const id = `hello-${kind}`;
  const typed = letters(message.trim());
  const contactWrong = refusal === "contact-too-long";
  const messageWrong = refusal === "empty" || refusal === "too-long";

  // Move focus to what needs attention: the box that's wrong, or the thank-you.
  useEffect(() => {
    if (messageWrong) messageBox.current?.focus();
    else if (contactWrong) contactBox.current?.focus();
  }, [refusal]);
  useEffect(() => {
    if (posted) thanks.current?.focus();
  }, [posted]);

  async function submit(event: Event) {
    event.preventDefault();
    if (busy) return;
    // Checked here too, so the person hears it at once (the API checks again).
    const clean = message.trim();
    const local: Refusal | null =
      clean === "" ? "empty" : letters(clean) > MAX_MESSAGE ? "too-long" : null;
    if (local) {
      setRefusal(local);
      return;
    }
    setBusy(true);
    setRefusal(null);
    const outcome = await send({ kind, message: clean, contact, turnstile: token });
    setBusy(false);
    if (outcome.sent) {
      setMessage("");
      setContact("");
      setPosted(outcome.url);
    } else {
      setRefusal(outcome.why);
    }
    // A Turnstile token works once: start a fresh check for the next try.
    if (siteKey) setRound((r) => r + 1);
  }

  if (posted) {
    return (
      <section class="panel" aria-labelledby={`${id}-heading`}>
        <h2 id={`${id}-heading`}>{words.heading}</h2>
        <p class="ok" role="status" tabIndex={-1} ref={thanks}>
          {text.sent}
        </p>
        <a href={posted}>{text.seePost}</a>
        <button type="button" class="link" onClick={() => setPosted(null)}>
          {text.sendAnother}
        </button>
      </section>
    );
  }

  return (
    <section class="panel" aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>{words.heading}</h2>
      <form onSubmit={submit} onFocusIn={() => setStarted(true)} noValidate>
        {/* Disabled while sending, so nothing changes under the person's feet. */}
        <fieldset disabled={busy}>
          <label for={`${id}-message`}>{words.label}</label>
          <textarea
            id={`${id}-message`}
            ref={messageBox}
            rows={5}
            value={message}
            aria-invalid={messageWrong}
            onInput={(e) => {
              setMessage(e.currentTarget.value);
              if (refusal) setRefusal(null); // an old warning goes once they type again
            }}
            aria-describedby={`${id}-public ${id}-left`}
          />
          <p id={`${id}-public`} class="small muted">
            {text.messageHint}
          </p>
          {/* Only near the limit, and not a live region: a screen reader would read it out
              on every key press. */}
          <p id={`${id}-left`} class="small muted">
            {MAX_MESSAGE - typed <= COUNT_FROM ? lettersLeft(typed) : ""}
          </p>
          <label for={`${id}-contact`}>{words.contactLabel}</label>
          <input
            id={`${id}-contact`}
            ref={contactBox}
            type="text"
            autocomplete="off"
            maxLength={MAX_CONTACT}
            value={contact}
            aria-invalid={contactWrong}
            onInput={(e) => setContact(e.currentTarget.value)}
            aria-describedby={`${id}-hint`}
          />
          <p id={`${id}-hint`} class="small muted">
            {words.contactHint}
          </p>
        </fieldset>
        {siteKey &&
          started &&
          (checkFailed ? (
            <p class="warn" role="alert">
              {text.checkFailed}
            </p>
          ) : (
            <Turnstile
              siteKey={siteKey}
              onToken={setToken}
              round={round}
              onFail={() => setCheckFailed(true)}
            />
          ))}
        {refusal && (
          <p class="warn" role="alert">
            {text.errors[refusal]}
          </p>
        )}
        <button type="submit" class="button" disabled={busy}>
          {busy ? text.sending : words.send}
        </button>
      </form>
    </section>
  );
}
