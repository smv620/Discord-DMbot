/** @jsxImportSource preact */
// "Write to the DMbot team" (#1340): a private ticket from a person on a paid plan, and the
// public Q&A link for everyone else. The message goes only to the DMbot team (the API keeps it
// in a table the website can't read back), so nothing here shows it again after sending.
import { useEffect, useRef, useState } from "preact/hooks";

import { QA_URL, text } from "../content/account";
import Turnstile from "../hello/Turnstile";
import { type AccountApi, ApiError } from "./api";

/** Counted like the API counts them: an emoji is one, not two. */
const letters = (s: string): number => Array.from(s).length;

export const MAX_SUBJECT = 120;
export const MAX_TICKET = 4000;

interface Props {
  api: AccountApi;
  /** Cloudflare Turnstile's site key, or "" to skip the check (local testing). */
  siteKey: string;
  /** Try It: adds one line, "Questions about buying a plan are welcome too." (#1435). */
  tryIt?: boolean;
  /** Called once a ticket is kept, so the list of the person's questions can be read again. */
  onSent?: () => void;
}

export function Support({ api, siteKey, tryIt, onSent }: Props) {
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [token, setToken] = useState("");
  const [round, setRound] = useState(0);
  const [busy, setBusy] = useState(false);
  // The person check starts when someone begins to write, not for every reader.
  const [started, setStarted] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // Sent, and whether the team can reply by email (the account has an address); null: not sent.
  const [sent, setSent] = useState<{ reply: boolean } | null>(null);
  const subjectBox = useRef<HTMLInputElement>(null);
  const messageBox = useRef<HTMLTextAreaElement>(null);
  const thanks = useRef<HTMLParagraphElement>(null);
  const problemBox = useRef<HTMLParagraphElement>(null);
  // Read before re-rendering: two taps or an Enter repeat in the same moment mustn't send two
  // tickets (the second would use up the 10 minutes, or a spent person-check token).
  const inFlight = useRef(false);
  const wasSent = useRef(false);

  // Focus follows what happened: the thank-you after a send, the warning after a refusal (a
  // disabled form drops focus to the page), the subject box when writing another.
  useEffect(() => {
    if (sent) thanks.current?.focus();
    else if (wasSent.current) subjectBox.current?.focus();
    wasSent.current = sent !== null;
  }, [sent]);
  useEffect(() => {
    if (problem) problemBox.current?.focus();
  }, [problem]);

  async function submit(event: Event) {
    event.preventDefault();
    if (inFlight.current || !api.sendSupport) return;
    const cleanSubject = subject.trim();
    const cleanMessage = message.trim();
    // Said here too, so the person hears it at once (the API checks again).
    const local =
      cleanSubject === "" || cleanMessage === ""
        ? "ticket-empty"
        : letters(cleanSubject) > MAX_SUBJECT
          ? "subject-too-long"
          : letters(cleanMessage) > MAX_TICKET
            ? "ticket-too-long"
            : null;
    if (local) {
      setProblem(text.supportErrors[local] ?? null);
      (local === "ticket-empty" && cleanSubject !== "" ? messageBox : subjectBox).current?.focus();
      return;
    }
    // No tick yet (or the check can't run): the API would only refuse it, so don't ask.
    if (siteKey && !token) {
      if (!checkFailed) setProblem(text.supportErrors["not-human"] ?? null);
      setStarted(true);
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      const receipt = await api.sendSupport({
        subject: cleanSubject,
        message: cleanMessage,
        turnstile: token,
      });
      setSubject("");
      setMessage("");
      setProblem(null);
      setSent(receipt);
      onSent?.();
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      // "Tick the box" means nothing when the site has no person check to tick.
      const shown = kind === "not-human" && !siteKey ? "server" : kind;
      setProblem(text.supportErrors[shown] ?? text.supportErrors["server"] ?? null);
    } finally {
      inFlight.current = false;
      setBusy(false);
      // A used token is spent whether or not it worked. Clearing it here (not only through the
      // new round) matters after a success: the check is unmounted then, so it never hears
      // about the new round, and the next ticket would carry the old token.
      setToken("");
      // A Turnstile token works once: start a fresh check for the next try.
      if (siteKey) setRound((r) => r + 1);
    }
  }

  if (sent) {
    return (
      <section class="panel" aria-labelledby="support-heading">
        <h3 id="support-heading">{text.supportHeading}</h3>
        <p class="ok" role="status" tabIndex={-1} ref={thanks}>
          {sent.reply ? text.supportSent : text.supportSentNoEmail}
        </p>
        <button
          type="button"
          class="link"
          onClick={() => {
            setProblem(null);
            setSent(null);
          }}
        >
          {text.supportAnother}
        </button>
      </section>
    );
  }

  const left = MAX_TICKET - letters(message.trim());
  return (
    <section class="panel" aria-labelledby="support-heading">
      <h3 id="support-heading">{text.supportHeading}</h3>
      <p>{text.supportIntro}</p>
      {tryIt && <p>{text.supportIntroTryIt}</p>}
      <form onSubmit={submit} onFocusIn={() => setStarted(true)} noValidate>
        {/* Disabled while sending, so nothing changes under the person's feet. */}
        <fieldset disabled={busy}>
          <label for="support-subject">{text.supportSubjectLabel}</label>
          <input
            id="support-subject"
            ref={subjectBox}
            type="text"
            autocomplete="off"
            value={subject}
            onInput={(e) => {
              setSubject(e.currentTarget.value);
              if (problem) setProblem(null);
            }}
          />
          <label for="support-message">{text.supportMessageLabel}</label>
          <textarea
            id="support-message"
            ref={messageBox}
            rows={6}
            value={message}
            aria-describedby="support-hint"
            onInput={(e) => {
              setMessage(e.currentTarget.value);
              if (problem) setProblem(null);
            }}
          />
          <p id="support-hint" class="small muted">
            {text.supportMessageHint}
            {/* Only near the limit, and not a live region: a screen reader would read it out
                on every key press. */}
            {left <= 200 && ` ${left < 0 ? `${-left} too many` : `${left} left`}`}
          </p>
        </fieldset>
        {/* Normal size, not small grey: it is the one thing to read before Send. */}
        <p>
          {text.supportPrivacy} {text.supportPublicLead} <a href={QA_URL}>{text.qaLink}</a>.
        </p>
        {siteKey &&
          started &&
          (checkFailed ? (
            <div class="stack">
              <p class="warn" role="alert">
                {text.supportCheckFailed}
              </p>
              <button
                type="button"
                class="button secondary"
                onClick={() => {
                  setCheckFailed(false);
                  setStarted(true);
                }}
              >
                {text.supportCheckAgain}
              </button>
            </div>
          ) : (
            <Turnstile
              siteKey={siteKey}
              onToken={setToken}
              round={round}
              onFail={() => setCheckFailed(true)}
            />
          ))}
        {problem && (
          <p class="warn" role="alert" tabIndex={-1} ref={problemBox}>
            {problem}
          </p>
        )}
        <button type="submit" class="button" disabled={busy}>
          {busy ? text.supportSending : text.supportSend}
        </button>
      </form>
    </section>
  );
}

/** For everyone who can't write privately: no account yet, or an API that doesn't have it yet. */
export function PublicQuestions() {
  return (
    <section class="panel" aria-labelledby="qa-heading">
      <h3 id="qa-heading">{text.qaHeading}</h3>
      <p>{text.qaText}</p>
      <p>
        <a href={QA_URL}>{text.qaLink}</a> · <a href="/hello">{text.helloLink}</a>
      </p>
    </section>
  );
}
